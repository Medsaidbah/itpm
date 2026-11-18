"""What a person has done, in figures (release 1.1, see docs/ROADMAP.md). Only facts the app already records.

The figures, for one person:
- done in the last 30 / 90 days: tasks assigned to them, now done, whose completion time (Task.completed_at,
  written by the app when a task becomes done, and cleared if it is opened again) is in that period.
- open: tasks assigned to them that aren't done, in projects that are still open (like the "My tasks" page).
- overdue: those open tasks whose due date is before today.
- done on time: among the tasks done in the last 90 days that had a due date, the share that were done on or
  before that date (the day of completion in Nouakchott time). Tasks without a due date can't be late, so they
  don't count. No such task: no rate (None, shown as a dash).
- comments and files: what they wrote and added in the last 90 days (comments and files deleted since are gone).
- last activity: the newest thing they did that the app records: a change to a project, a task or a team (from
  the history), a comment or a file. Logging in isn't activity.

Who sees what (figures_scope): the person themselves and admins see everything. A manager of some of the
person's projects (the project's manager or a co-manager) sees only the work in the projects they manage.
Everyone else sees no figures. No rankings: the admins' overview is sorted by name.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, F, Max, Q
from django.utils import timezone

from .models import Attachment, Comment, Project, ProjectMember, Task

SHORT_PERIOD = 30  # days
LONG_PERIOD = 90  # days


@dataclass
class Figures:
    done_short: int = 0
    done_long: int = 0
    open: int = 0
    overdue: int = 0
    timed: int = 0  # done in the long period, with a due date
    on_time: int = 0  # of those, done on or before their due date
    comments: int = 0
    files: int = 0
    last_activity: datetime | None = None

    @property
    def on_time_rate(self):
        """A percentage, or None when no task can be measured (shown as a dash, never as 0 % or 100 %)."""
        return round(self.on_time * 100 / self.timed) if self.timed else None


class Scope:
    """The projects whose work a viewer may see in figures: all of them, some of them, or none."""

    def __init__(self, projects=None, everything=False):
        self.everything = everything
        self.projects = projects  # a Project queryset when only some projects count

    @property
    def shows_figures(self):
        return self.everything or self.projects is not None

    def limit(self, queryset, project_field):
        """The queryset, kept to the projects of this scope (project_field: the path to the project)."""
        if self.everything:
            return queryset
        return queryset.filter(**{f"{project_field}__in": self.projects})


EVERYTHING = Scope(everything=True)
NOTHING = Scope()


def managed_by(user):
    """The projects this person manages: as the project's manager, or as a co-manager."""
    co_managed = ProjectMember.objects.filter(user=user, is_manager=True).values("project")
    return Project.objects.filter(Q(manager=user) | Q(pk__in=co_managed))


def figures_scope(viewer, person):
    """Which of the person's work the viewer may see in figures (the rules at the top of this file)."""
    if not viewer.is_authenticated:
        return NOTHING
    if viewer.sees_everything or viewer.pk == person.pk:
        return EVERYTHING
    managed = managed_by(viewer)
    # Only when the person is in the team of one of those projects: their manager, or a member
    shared = managed.filter(Q(manager=person) | Q(memberships__user=person)).values("pk")
    if not shared.exists():
        return NOTHING
    return Scope(projects=managed)


def figures_for(people_ids, scope=EVERYTHING):
    """{person's id: Figures} for these people, within the scope. Always the same number of database queries,
    however many people (the admins' overview shows everyone at once)."""
    people_ids = list(people_ids)
    figures = {pk: Figures() for pk in people_ids}
    if not people_ids or not scope.shows_figures:
        return figures
    now, today = timezone.now(), timezone.localdate()
    short_start, long_start = now - timedelta(days=SHORT_PERIOD), now - timedelta(days=LONG_PERIOD)

    # Tasks: one query for the done, open, overdue and on-time counts
    done = Q(status=Task.Status.DONE)
    done_long = done & Q(completed_at__gte=long_start)
    is_open = ~done & Q(project__status__in=Project.ACTIVE_STATUSES)
    timed = done_long & Q(due_date__isnull=False)
    tasks = scope.limit(Task.objects.filter(assignee__in=people_ids), "project")
    for row in tasks.values("assignee").annotate(
        done_short=Count("id", filter=done & Q(completed_at__gte=short_start)),
        done_long=Count("id", filter=done_long),
        open=Count("id", filter=is_open),
        overdue=Count("id", filter=is_open & Q(due_date__lt=today)),
        timed=Count("id", filter=timed),
        on_time=Count("id", filter=timed & Q(completed_at__date__lte=F("due_date"))),
    ).order_by():
        person = figures[row.pop("assignee")]
        for name, value in row.items():
            setattr(person, name, value)

    # Comments and files: how many in the long period, and the newest of all (for the last activity)
    newest = {pk: [] for pk in people_ids}
    for model, who, when, counter in ((Comment, "author", "created_at", "comments"),
                                      (Attachment, "uploaded_by", "uploaded_at", "files")):
        rows = scope.limit(model.objects.filter(**{f"{who}__in": people_ids}), "task__project")
        for row in rows.values(who).annotate(
            recent=Count("id", filter=Q(**{f"{when}__gte": long_start})), last=Max(when)
        ).order_by():
            setattr(figures[row[who]], counter, row["recent"])
            newest[row[who]].append(row["last"])

    # The history: changes to projects, tasks and teams
    for versions, project_field in ((Project.history, "id"), (Task.history, "project_id"),
                                    (ProjectMember.history, "project_id")):
        rows = scope.limit(versions.filter(history_user__in=people_ids), project_field)
        for row in rows.values("history_user").annotate(last=Max("history_date")).order_by():
            newest[row["history_user"]].append(row["last"])

    for pk, moments in newest.items():
        figures[pk].last_activity = max(moments) if moments else None
    return figures
