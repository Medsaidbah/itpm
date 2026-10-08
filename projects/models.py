from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Q
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


class Attachment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="attachments/%Y/%m/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attachments"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.file.name
