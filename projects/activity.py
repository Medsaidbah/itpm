"""The activity feed: who did what, and when.

Nothing new is saved for this. The app already keeps everything the feed needs:
  - every version of every project and task, with who saved it and when
    (django-simple-history, added in Step 3);
  - comments and files, with their author and date (Step 7);
  - who added whom to a project's team, or removed them, and when (Stage 16).
This file reads them, newest first, and turns them into short sentences.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.utils.timesince import timesince

from .models import Attachment, Comment, Project, ProjectMember, Task

PAGE_SIZE = 30  # events per page

# The fields the feed talks about, with the names people see on the forms.
# Other fields, like the order of the cards on the board, are left out.
PROJECT_FIELDS = {
    "name": "Name",
    "status": "Status",
    "priority": "Priority",
    "manager": "Manager",
    "start_date": "Start date",
    "end_date": "End date",
    "description": "Description",
}
TASK_FIELDS = {
    "title": "Title",
    "status": "Status",
    "priority": "Priority",
    "assignee": "Assigned to",
    "start_date": "Start date",
    "due_date": "Due date",
    "parent": "Main task",
    "is_milestone": "Milestone",
    "description": "Description",
}


@dataclass
class Event:
    """One line of the feed, like "Sara updated the task Install the firewall"."""

    when: datetime
    who: object  # the user who did it (None when nobody was logged in, like in the shell)
    icon: str  # a Tabler icon, like "ti-pencil"
    color: str  # the icon's color, like "blue"
    action: str  # what they did, like "updated the task"
    name: str  # the name of the task or project
    url: str = ""  # a link to it ("" when it has been deleted since)
    project: object = None  # the project that a task, comment or file belongs to
    changes: list = field(default_factory=list)  # (field, old value, new value) for each change
    text: str = ""  # the text of a comment
    attachment: object = None  # an uploaded file

    @property
    def ago(self):
        """Like "5 minutes ago", or "just now" during the first minute."""
        if timezone.now() - self.when < timedelta(minutes=1):
            return "just now"
        return f"{timesince(self.when)} ago"


# ---------- Turning saved versions into sentences ----------


def readable(version, name, people, tasks):
    """One field of a saved version, written the way people read it."""
    # For a link to another row (like "assignee") this is its number, like 3
    value = getattr(version, version._meta.get_field(name).attname)
    if name == "status":
        return version.get_status_display()  # "in_progress" becomes "In progress"
    if name == "priority":
        return version.get_priority_display()
    if name in ("assignee", "manager"):
        if value is None:
            return "Unassigned"
        return str(people[value]) if value in people else "A deleted user"
    if name == "parent":
        if value is None:
            return "None (main task)"
        return tasks[value].title if value in tasks else "A deleted task"
    if name == "is_milestone":
        return "Yes" if value else "No"
    if isinstance(value, date):
        return date_format(value, "d M Y")
    return value or "Not set"


def changes_between(old, new, fields, people, tasks):
    """The fields that are different in the newer version: [(field, old value, new value), ...]"""
    changes = []
    for name, label in fields.items():
        attname = new._meta.get_field(name).attname
        if getattr(old, attname) == getattr(new, attname):
            continue
        if name == "description":  # too long to show, so we only say that it changed
            changes.append((label, None, "changed"))
        else:
            changes.append((label, readable(old, name, people, tasks), readable(new, name, people, tasks)))
    return changes


# simple_history marks each saved version with a sign:
# "+" = created, "~" = changed, "-" = deleted


def project_event(version, projects, people):
    project = projects.get(version.id)  # None if the project has been deleted since
    event = Event(
        when=version.history_date,
        who=version.history_user,
        icon="ti-pencil",
        color="blue",
        action="updated the project",
        name=version.name,
        url=project.get_absolute_url() if project else "",
    )
    if version.history_type == "+":
        event.icon, event.color, event.action = "ti-plus", "green", "created the project"
        event.changes = [("Manager", None, readable(version, "manager", people, {}))]
    elif version.history_type == "-":
        event.icon, event.color, event.action, event.url = "ti-trash", "red", "deleted the project", ""
    elif version.previous:
        event.changes = changes_between(version.previous, version, PROJECT_FIELDS, people, {})
        if not event.changes:
            return None  # saved without changing anything that the feed shows
    return event


def task_event(version, tasks, projects, people):
    task = tasks.get(version.id)  # None if the task has been deleted since
    event = Event(
        when=version.history_date,
        who=version.history_user,
        icon="ti-pencil",
        color="blue",
        action="updated the task",
        name=version.title,
        url=task.get_absolute_url() if task else "",
        project=projects.get(version.project_id),
    )
    if version.history_type == "+":
        event.icon, event.color = "ti-plus", "green"
        event.action = "created the subtask" if version.parent_id else "created the task"
        if version.assignee_id:
            event.changes.append(("Assigned to", None, readable(version, "assignee", people, tasks)))
        if version.due_date:
            event.changes.append(("Due date", None, readable(version, "due_date", people, tasks)))
    elif version.history_type == "-":
        event.icon, event.color, event.action, event.url = "ti-trash", "red", "deleted the task", ""
    elif version.previous and version.previous.project_id != version.project_id:
        pass  # moved here from another project: its earlier values belong to that project, so they aren't shown
    elif version.previous:
        event.changes = changes_between(version.previous, version, TASK_FIELDS, people, tasks)
        if not event.changes:
            return None  # saved without changing anything that the feed shows
        if version.status == Task.Status.DONE and version.previous.status != Task.Status.DONE:
            event.icon, event.color = "ti-check", "green"  # the task was just finished
    return event


def team_event(version, projects, people):
    """Someone added a person to a project's team, or removed them from it."""
    project = projects.get(version.project_id)
    if project is None:
        return None  # the project was deleted since: the feed already says so
    person = people.get(version.user_id)
    name = str(person) if person else "a deleted user"
    if version.history_type == "+":
        icon, color, action = "ti-user-plus", "green", f"added {name} to the team of"
    elif version.history_type == "-":
        icon, color, action = "ti-user-minus", "orange", f"removed {name} from the team of"
    else:
        return None  # "~": nothing that the feed shows changed
    return Event(
        when=version.history_date,
        who=version.history_user,
        icon=icon,
        color=color,
        action=action,
        name=project.name,
        url=project.get_absolute_url(),
    )


def comment_event(comment):
    return Event(
        when=comment.created_at,
        who=comment.author,
        icon="ti-message",
        color="azure",
        action="commented on",
        name=comment.task.title,
        url=f"{comment.task.get_absolute_url()}#comments",
        project=comment.task.project,
        text=comment.body,
    )


def file_event(attachment):
    return Event(
        when=attachment.uploaded_at,
        who=attachment.uploaded_by,
        icon="ti-paperclip",
        color="purple",
        action="added a file to",
        name=attachment.task.title,
        url=f"{attachment.task.get_absolute_url()}#files",
        project=attachment.task.project,
        attachment=attachment,
    )


# ---------- Reading the events from the database ----------


def with_previous_id(versions):
    """Adds "previous_id" to each saved version: the number of the version saved just before it,
    for the same project or task (None for the first one). It's done inside the same query."""
    earlier = versions.model.objects.filter(id=OuterRef("id"), history_id__lt=OuterRef("history_id"))
    return versions.annotate(
        previous_id=Subquery(earlier.order_by("-history_id").values("history_id")[:1])
    )


def get_events(viewer, project=None, task=None, person=None, before=None, limit=PAGE_SIZE):
    """The newest events that the viewer may see and that match the filters, newest first.
    Returns (events, older_than). older_than is the moment where the next page starts,
    or None when there is nothing older."""

    # 1. The five places where things are recorded
    project_versions = with_previous_id(Project.history.select_related("history_user"))
    task_versions = with_previous_id(Task.history.select_related("history_user"))
    comments = Comment.objects.select_related("author", "task__project")
    files = Attachment.objects.select_related("uploaded_by", "task__project")
    team_versions = ProjectMember.history.select_related("history_user")  # people added to a team or removed

    # 2. Only what happened in the projects the viewer can see. Admins see everything,
    #    even what happened in projects deleted since.
    if not viewer.is_admin:
        visible = Project.objects.visible_to(viewer).values("pk")
        project_versions = project_versions.filter(id__in=visible)
        task_versions = task_versions.filter(project_id__in=visible)
        comments = comments.filter(task__project__in=visible)
        files = files.filter(task__project__in=visible)
        team_versions = team_versions.filter(project_id__in=visible)

    # 3. The filters
    if project:
        project_versions = project_versions.filter(id=project.pk)
        task_versions = task_versions.filter(project_id=project.pk)
        comments = comments.filter(task__project=project)
        files = files.filter(task__project=project)
        team_versions = team_versions.filter(project_id=project.pk)
    if task:
        project_versions = project_versions.none()
        task_versions = task_versions.filter(id=task.pk)
        comments = comments.filter(task=task)
        files = files.filter(task=task)
        team_versions = team_versions.none()  # a team belongs to a project, not to a task
    if person:
        project_versions = project_versions.filter(history_user=person)
        task_versions = task_versions.filter(history_user=person)
        comments = comments.filter(author=person)
        files = files.filter(uploaded_by=person)
        team_versions = team_versions.filter(history_user=person)
    if before:  # "Show older activity": only what happened before that moment
        project_versions = project_versions.filter(history_date__lt=before)
        task_versions = task_versions.filter(history_date__lt=before)
        comments = comments.filter(created_at__lt=before)
        files = files.filter(uploaded_at__lt=before)
        team_versions = team_versions.filter(history_date__lt=before)

    # 4. The newest of each kind, all mixed together and sorted by date.
    #    We take one more than a page, to find out if there is anything older.
    found = (
        [(v.history_date, "project", v) for v in project_versions.order_by("-history_date", "-history_id")[: limit + 1]]
        + [(v.history_date, "task", v) for v in task_versions.order_by("-history_date", "-history_id")[: limit + 1]]
        + [(c.created_at, "comment", c) for c in comments.order_by("-created_at")[: limit + 1]]
        + [(f.uploaded_at, "file", f) for f in files.order_by("-uploaded_at")[: limit + 1]]
        + [(v.history_date, "team", v) for v in team_versions.order_by("-history_date", "-history_id")[: limit + 1]]
    )
    found.sort(key=lambda item: item[0], reverse=True)  # newest first
    page = found[:limit]
    older_than = page[-1][0] if len(found) > limit else None

    # 5. Load at once what the sentences need: the version saved just before each change
    #    (to see what changed), and the names of people, tasks and projects.
    page_project_versions = [item for _, kind, item in page if kind == "project"]
    page_task_versions = [item for _, kind, item in page if kind == "task"]
    for versions, model in ((page_project_versions, Project), (page_task_versions, Task)):
        previous = model.history.in_bulk([v.previous_id for v in versions if v.previous_id])
        for version in versions:
            version.previous = previous.get(version.previous_id)

    people = get_user_model().objects.in_bulk()  # everyone (there aren't many people)
    task_ids = set()
    project_ids = {version.id for version in page_project_versions}
    project_ids |= {item.project_id for _, kind, item in page if kind == "team"}
    for version in page_task_versions:
        task_ids.add(version.id)
        project_ids.add(version.project_id)
        for v in (version, version.previous):
            if v and v.parent_id:
                task_ids.add(v.parent_id)  # to show the names of main tasks
    tasks = Task.objects.in_bulk(task_ids)  # tasks deleted since then are missing
    projects = Project.objects.in_bulk(project_ids)

    # 6. One sentence per event
    events = []
    for _, kind, item in page:
        if kind == "project":
            event = project_event(item, projects, people)
        elif kind == "task":
            event = task_event(item, tasks, projects, people)
        elif kind == "comment":
            event = comment_event(item)
        elif kind == "team":
            event = team_event(item, projects, people)
        else:
            event = file_event(item)
        if event:  # None for a save that changed nothing that the feed shows
            events.append(event)
    return events, older_than


# ---------- The Activity page ----------


def read_moment(text):
    """The ?before= part of the address as a date and time, or None if it isn't one."""
    try:
        moment = parse_datetime(text)
    except ValueError:  # it looks like a date but isn't a real one (like month 13)
        return None
    if moment and timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


def group_by_day(events):
    """[(day, events of that day), ...] for the headings "Today", "Yesterday", ..."""
    days = []
    for event in events:
        day = timezone.localtime(event.when).date()
        if not days or days[-1][0] != day:
            days.append((day, []))
        days[-1][1].append(event)
    return days


@login_required
def activity_feed(request):
    """What happened, newest first, in the projects the person can see (admins: everything).
    ?project=, ?person= and ?task= in the address filter it; ?before= shows older events."""
    projects = Project.objects.visible_to(request.user)
    # The people in the filter: everyone for admins. For everyone else, the people who can act
    # in their projects: the people in their teams, and the admins.
    User = get_user_model()
    if request.user.is_admin:
        people = User.objects.all()
    else:
        people = User.objects.filter(Q(pk__in=projects.people().values("pk")) | Q(role=User.Role.ADMIN))

    project = person = task = None
    if request.GET.get("project", "").isdigit():
        project = get_object_or_404(projects, pk=request.GET["project"])
    if request.GET.get("person", "").isdigit():
        person = get_object_or_404(people, pk=request.GET["person"])
    if request.GET.get("task", "").isdigit():
        task = get_object_or_404(Task.objects.visible_to(request.user), pk=request.GET["task"])
    before = read_moment(request.GET.get("before", ""))

    events, older_than = get_events(request.user, project=project, person=person, task=task, before=before)

    today = timezone.localdate()
    context = {
        "days": group_by_day(events),
        "today": today,
        "yesterday": today - timedelta(days=1),
        # Where the "Show older activity" button starts ("" when there is nothing older)
        "older_than": older_than.isoformat() if older_than else "",
        "is_older_page": before is not None,
        "project": project,
        "person": person,
        "task": task,
        # Don't repeat the project's name on every line when we only show one project
        "hide_project": bool(project or task),
        "projects": projects.order_by("name"),
        "people": people.order_by("first_name", "last_name", "username"),
    }
    return render(request, "projects/activity.html", context)
