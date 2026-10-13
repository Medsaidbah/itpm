import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count, Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from simple_history.models import HistoricalRecords


class Priority(models.TextChoices):
    LOW = "low", gettext_lazy("Low")
    MEDIUM = "medium", gettext_lazy("Medium")
    HIGH = "high", gettext_lazy("High")
    CRITICAL = "critical", gettext_lazy("Critical")


# Badge colors shown on the pages (these are Tabler color names)
PRIORITY_COLORS = {"low": "secondary", "medium": "cyan", "high": "orange", "critical": "red"}


class ProjectQuerySet(models.QuerySet):
    def with_task_counts(self):
        """Count every project's tasks in one database query, so long lists stay fast."""
        done = Q(tasks__status=Task.Status.DONE)
        late = Q(tasks__due_date__lt=timezone.localdate()) & ~done
        return self.annotate(
            task_total=Count("tasks"),
            task_done=Count("tasks", filter=done),
            task_overdue=Count("tasks", filter=late),
        )

    def visible_to(self, user):
        """The projects this person can see and open: all of them for admins;
        for everyone else, the projects they manage or whose team they are in.
        This is the only place the rule is written: every page, export and download uses it."""
        if not user.is_authenticated:
            return self.none()  # nobody logged in: nothing (the pages ask to log in first anyway)
        if user.is_admin:
            return self
        in_team = ProjectMember.objects.filter(user=user).values("project")
        return self.filter(Q(manager=user) | Q(pk__in=in_team))

    def people(self):
        """Everyone in the teams of these projects: their managers and their members."""
        return get_user_model().objects.filter(
            Q(pk__in=self.values("manager")) | Q(pk__in=ProjectMember.objects.filter(project__in=self).values("user"))
        )


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", gettext_lazy("Planned")
        IN_PROGRESS = "in_progress", gettext_lazy("In progress")
        ON_HOLD = "on_hold", gettext_lazy("On hold")
        COMPLETED = "completed", gettext_lazy("Completed")
        CANCELLED = "cancelled", gettext_lazy("Cancelled")

    # Projects that aren't finished yet
    ACTIVE_STATUSES = [Status.PLANNED, Status.IN_PROGRESS, Status.ON_HOLD]
    STATUS_COLORS = {
        "planned": "purple",
        "in_progress": "blue",
        "on_hold": "yellow",
        "completed": "green",
        "cancelled": "secondary",
    }

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    # Anyone whose account is active can manage a project (only admins choose who)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_projects",
        limit_choices_to={"is_active": True},
    )
    # The team: the people who work on the project, besides its manager.
    # Each line of the team is a ProjectMember (below), which also remembers who added the person.
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ProjectMember",
        through_fields=("project", "user"),
        related_name="member_projects",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Adds with_task_counts() to Project.objects
    objects = ProjectQuerySet.as_manager()

    # Records every change: what changed, who changed it, and when
    history = HistoricalRecords()

    # Not saved in the database. The pages set saved_by to the person saving the project;
    # after saving, kept_in_team is the previous manager when they were just kept in the team.
    saved_by = None
    kept_in_team = None

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Who managed the project before this save (nobody for a new project)
        previous_manager_id = (
            Project.objects.filter(pk=self.pk).values_list("manager_id", flat=True).first() if self.pk else None
        )
        super().save(*args, **kwargs)

        # A new manager: the previous one stays in the team, as a member, so they keep their access.
        # It's done here so that every way of changing the manager does it (pages, admin panel...).
        self.kept_in_team = None
        if previous_manager_id and previous_manager_id != self.manager_id:
            previous_manager = get_user_model().objects.get(pk=previous_manager_id)
            if self.add_to_team(previous_manager, added_by=self.saved_by):
                self.kept_in_team = previous_manager

    def get_absolute_url(self):
        """The address of this project's page."""
        return reverse("project_detail", args=[self.pk])

    def can_edit(self, user):
        """Who may change this project, its tasks and its team: admins and the project's manager.
        This is the only place the rule is written, so it's easy to change later."""
        return user.is_admin or user.pk == self.manager_id

    def can_manage_team(self, user):
        """Who may add people to the team and remove them: the same people who can edit the project."""
        return self.can_edit(user)

    def can_change_manager(self, user):
        """Who may choose the project's manager: admins only."""
        return user.is_admin

    def team(self):
        """Everyone in the team: the manager first, then the members by name."""
        members = sorted(
            (person for person in self.members.all() if person.pk != self.manager_id),
            key=lambda person: str(person).lower(),
        )
        return [self.manager] + members

    def team_queryset(self):
        """The people in the team (the manager and the members), as a database query."""
        return get_user_model().objects.filter(
            Q(pk=self.manager_id) | Q(pk__in=self.memberships.values("user"))
        )

    def add_to_team(self, user, added_by=None):
        """Puts this person in the team, if they aren't in it already.
        Returns True if they were added, False if they were in the team already."""
        if user.pk == self.manager_id:
            return False  # the manager is always part of the team
        _, added = self.memberships.get_or_create(user=user, defaults={"added_by": added_by})
        return added

    def open_tasks_of(self, user):
        """This person's tasks in this project that aren't done yet."""
        return self.tasks.filter(assignee=user).exclude(status=Task.Status.DONE)

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("The end date can't be before the start date.")})

    @property
    def progress(self):
        """Percentage of this project's tasks that are done (0 to 100)."""
        if hasattr(self, "task_total"):
            # The counts were already fetched by with_task_counts()
            total, done = self.task_total, self.task_done
        else:
            total = self.tasks.count()
            done = self.tasks.filter(status=Task.Status.DONE).count()
        if total == 0:
            return 0
        return round(done * 100 / total)

    @property
    def is_overdue(self):
        """The end date has passed, but the project isn't finished."""
        return (
            self.end_date is not None
            and self.status in self.ACTIVE_STATUSES
            and self.end_date < timezone.localdate()
        )

    @property
    def status_color(self):
        return self.STATUS_COLORS.get(self.status, "secondary")

    @property
    def priority_color(self):
        return PRIORITY_COLORS.get(self.priority, "secondary")


class ProjectMember(models.Model):
    """One person in a project's team. The project's manager is always part of the team
    (through Project.manager), so the manager doesn't need a line here. When a member becomes
    the manager, their line just stays: it's ignored while they are the manager."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    added_at = models.DateTimeField(auto_now_add=True)
    # Who added this person (empty for the teams that Step 16a filled in by itself)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # Remembers who added or removed whom, and when
    history = HistoricalRecords()

    class Meta:
        # A person is in a team only once
        constraints = [models.UniqueConstraint(fields=["project", "user"], name="unique_project_member")]

    def __str__(self):
        return _("%(person)s in %(project)s") % {"person": self.user, "project": self.project}


def people_visible_to(user):
    """The people whose profile (email, phone, picture, projects) this person can see:
    everyone for admins; for everyone else, themselves and the people they share a project with."""
    User = get_user_model()
    if not user.is_authenticated:
        return User.objects.none()
    if user.is_admin:
        return User.objects.all()
    teammates = Project.objects.visible_to(user).people().values("pk")
    return User.objects.filter(Q(pk=user.pk) | Q(pk__in=teammates))


class TaskQuerySet(models.QuerySet):
    def visible_to(self, user):
        """The tasks of the projects this person can see (see ProjectQuerySet.visible_to)."""
        if user.is_authenticated and user.is_admin:
            return self
        return self.filter(project__in=Project.objects.visible_to(user))


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", gettext_lazy("To do")
        IN_PROGRESS = "in_progress", gettext_lazy("In progress")
        BLOCKED = "blocked", gettext_lazy("Blocked")
        DONE = "done", gettext_lazy("Done")

    STATUS_COLORS = {"todo": "secondary", "in_progress": "blue", "blocked": "red", "done": "green"}

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    # Filled in only for subtasks: points to the main task
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="subtasks"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TODO)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        limit_choices_to={"is_active": True},
    )
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    is_milestone = models.BooleanField(default=False)
    # Tasks that must be finished before this one can start
    depends_on = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="blocking"
    )
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    position = models.PositiveIntegerField(default=0)  # order on the Kanban board
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Adds visible_to() to Task.objects
    objects = TaskQuerySet.as_manager()

    history = HistoricalRecords()

    # Not saved in the database. The pages set saved_by to the person saving the task;
    # after saving, joined_team says if the task's person was just put in the team (see save()).
    saved_by = None
    joined_team = False

    class Meta:
        ordering = ["position", "due_date", "id"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        """The address of this task's page."""
        return reverse("task_detail", args=[self.pk])

    def can_edit(self, user):
        """Who may create, edit and delete tasks: the people who can edit the project."""
        return self.project.can_edit(user)

    def can_update_status(self, user):
        """Who may change the status: the same people, plus the person the task is assigned to."""
        return self.can_edit(user) or (self.assignee_id is not None and self.assignee_id == user.pk)

    def clean(self):
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValidationError({"due_date": _("The due date can't be before the start date.")})
        if self.parent:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": _("A task can't be its own subtask.")})
            if self.parent.parent_id:
                raise ValidationError({"parent": _("A subtask can't have its own subtasks.")})
            if self.parent.project_id != self.project_id:
                raise ValidationError(
                    {"parent": _("A subtask must be in the same project as its main task.")}
                )
            if self.pk and self.subtasks.exists():
                raise ValidationError(
                    {"parent": _("This task has its own subtasks, so it can't become a subtask.")}
                )

    def save(self, *args, **kwargs):
        # Remember when the task was finished (we'll use this in reports)
        if self.status == self.Status.DONE:
            if self.completed_at is None:
                self.completed_at = timezone.now()
        else:
            self.completed_at = None
        super().save(*args, **kwargs)

        # The person an open task is given to is always in the project's team, so they can open it.
        # This adds them when an admin gives them the task, or when a finished task is opened again.
        self.joined_team = False
        if self.assignee_id and self.status != self.Status.DONE:
            self.joined_team = self.project.add_to_team(self.assignee, added_by=self.saved_by)

    @property
    def is_overdue(self):
        return (
            self.due_date is not None
            and self.status != self.Status.DONE
            and self.due_date < timezone.localdate()
        )

    @property
    def waiting_for(self):
        """The tasks this one waits for that aren't done yet."""
        return [task for task in self.depends_on.all() if task.status != self.Status.DONE]

    def waits_for(self, other):
        """True if this task waits for `other`, directly or through a chain of other tasks."""
        seen = set()
        to_check = [self]
        while to_check:
            task = to_check.pop()
            for dependency in task.depends_on.all():
                if dependency.pk == other.pk:
                    return True
                if dependency.pk not in seen:
                    seen.add(dependency.pk)
                    to_check.append(dependency)
        return False

    @property
    def status_color(self):
        return self.STATUS_COLORS.get(self.status, "secondary")

    @property
    def priority_color(self):
        return PRIORITY_COLORS.get(self.priority, "secondary")


class Comment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="comments"
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return _("Comment by %(person)s on %(task)s") % {"person": self.author, "task": self.task}

    def can_delete(self, user):
        """The author can delete a comment, and so can anyone who can edit the task."""
        return self.author_id == user.pk or self.task.can_edit(user)


class Attachment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="attachments/%Y/%m/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attachments"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.file.name

    @property
    def filename(self):
        """The file's name without its folders, for example "quote.pdf"."""
        return os.path.basename(self.file.name)

    @property
    def size(self):
        """The file's size in bytes, or None if the file is missing from the disk."""
        try:
            return self.file.size
        except OSError:
            return None

    def can_delete(self, user):
        """The person who uploaded a file can delete it, and so can anyone who can edit the task."""
        return self.uploaded_by_id == user.pk or self.task.can_edit(user)


@receiver(post_delete, sender=Attachment)
def delete_attachment_file(sender, instance, **kwargs):
    """When an attachment is deleted (on its own, or with its task), delete its file from the disk too.
    on_commit waits until the database change is final before touching the disk."""
    name, storage = instance.file.name, instance.file.storage
    if name:
        transaction.on_commit(lambda: storage.delete(name))
