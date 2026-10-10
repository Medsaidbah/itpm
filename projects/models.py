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
from simple_history.models import HistoricalRecords


class Priority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


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


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

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
    # Only active admins and managers can be chosen as the project manager
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_projects",
        limit_choices_to={"role__in": ["admin", "manager"], "is_active": True},
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

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        """The address of this project's page."""
        return reverse("project_detail", args=[self.pk])

    def can_edit(self, user):
        """Who may change this project: today, any admin or manager.
        This is the only place the rule is written, so it's easy to change later."""
        return user.can_manage

    def can_manage_team(self, user):
        """Who may add people to the team and remove them: admins and this project's manager."""
        return user.is_admin or user.pk == self.manager_id

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
            raise ValidationError({"end_date": "The end date can't be before the start date."})

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
        return f"{self.user} in {self.project}"


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        BLOCKED = "blocked", "Blocked"
        DONE = "done", "Done"

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

    history = HistoricalRecords()

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
            raise ValidationError({"due_date": "The due date can't be before the start date."})
        if self.parent:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A task can't be its own subtask."})
            if self.parent.parent_id:
                raise ValidationError({"parent": "A subtask can't have its own subtasks."})
            if self.parent.project_id != self.project_id:
                raise ValidationError(
                    {"parent": "A subtask must be in the same project as its main task."}
                )
            if self.pk and self.subtasks.exists():
                raise ValidationError(
                    {"parent": "This task has its own subtasks, so it can't become a subtask."}
                )

    def save(self, *args, **kwargs):
        # Remember when the task was finished (we'll use this in reports)
        if self.status == self.Status.DONE:
            if self.completed_at is None:
                self.completed_at = timezone.now()
        else:
            self.completed_at = None
        super().save(*args, **kwargs)

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
        return f"Comment by {self.author} on {self.task}"

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
