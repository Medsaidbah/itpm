"""Is a project behind schedule? (release 1.1, see docs/ROADMAP.md)

The progress a project should have by today (the "expected" progress) is compared with its real progress
(the weights of the done tasks ÷ the weights of all its tasks, see Project.progress; weighted since release 1.4):

- When at least one task has a due date, the expected progress is the weights of the tasks that were due before
  today over the weights of all the tasks. A task due today still has today; a task without a due date isn't
  expected yet. With every weight at 1, this is the share of the tasks due before today, as in release 1.1.
- When no task has a due date, it is the share of the project's days (start day through end day) that have
  passed before today.
- A project without tasks, or without any date, isn't measured: nothing is shown.

Only planned and in-progress projects are measured: a project on hold, completed or cancelled is never
behind schedule. The comparison uses the rounded percentages shown on the page, so what people see always
agrees with the flag."""

from dataclasses import dataclass

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

# A project is "behind schedule" when its progress is more than this many points below the expected progress
BEHIND_SCHEDULE_MARGIN = 10

BY_TASKS = "tasks"  # from the tasks' due dates
BY_DATES = "dates"  # from the project's start and end dates


@dataclass(frozen=True)
class Schedule:
    expected: int  # the percentage that should be done by today (0 to 100)
    actual: int  # the percentage that is done (Project.progress)
    basis: str  # BY_TASKS or BY_DATES

    @property
    def gap(self):
        """How many points the project is behind (negative when it is ahead)."""
        return self.expected - self.actual

    @property
    def is_behind(self):
        return self.gap > BEHIND_SCHEDULE_MARGIN


def time_elapsed(start, end, today):
    """The share of the project's days that have passed by today, from 0 to 100. The project runs from its
    start day through its end day, and today isn't over yet: like a task due today, the end day still has
    today, and 100 % is reached the day after."""
    days = (end - start).days + 1
    passed = min(max((today - start).days, 0), days)
    return round(passed * 100 / days)


def schedule_of(project):
    """The project's Schedule, or None when the project isn't measured (see above)."""
    if project.status not in (project.Status.PLANNED, project.Status.IN_PROGRESS):
        return None
    today = timezone.localdate()
    if hasattr(project, "weight_due"):
        # The counts and the weights were already fetched by with_task_counts()
        total, dated = project.task_total, project.task_dated
        total_weight, due_weight = project.weight_total, project.weight_due
    else:
        total = project.tasks.count()
        dated = project.tasks.filter(due_date__isnull=False).count()
        weights = project.tasks.aggregate(total=Coalesce(Sum("weight"), 0),
                                          due=Coalesce(Sum("weight", filter=Q(due_date__lt=today)), 0))
        total_weight, due_weight = weights["total"], weights["due"]
    if total == 0:
        return None
    if dated:
        # Release 1.4: weighted, the weights of the tasks due before today over the weights of all the tasks
        return Schedule(expected=round(due_weight * 100 / total_weight), actual=project.progress, basis=BY_TASKS)
    if project.start_date and project.end_date:
        expected = time_elapsed(project.start_date, project.end_date, today)
        return Schedule(expected=expected, actual=project.progress, basis=BY_DATES)
    return None
