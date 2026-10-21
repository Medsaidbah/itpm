from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, F, Prefetch, Q
from django.db.models.functions import TruncWeek
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext, pgettext
from django.views.decorators.http import require_POST

from .activity import get_events
from .emails import email_new_assignee
from .forms import (
    MAX_UPLOAD_MB,
    AttachmentForm,
    CommentForm,
    ProjectDeleteForm,
    ProjectForm,
    TaskForm,
    TeamMemberForm,
)
from .models import Attachment, Comment, Project, ProjectMember, Task
from .notifications import notify_assigned

# ---------- Who sees what ----------
# People only see the projects they manage or whose team they are in; admins see all of them
# (the rule is written once, in models.py: Project.objects.visible_to). Every page below gets its
# projects, tasks, comments and files through these two helpers, so someone outside a project
# gets "Page not found" for anything in it, as if it didn't exist.


def visible_projects(request):
    """The projects the person who is logged in can see."""
    return Project.objects.visible_to(request.user)


def visible_tasks(request):
    """The tasks of the projects the person who is logged in can see."""
    return Task.objects.visible_to(request.user)


@login_required
def home(request):
    """The first page people see after logging in."""
    today = timezone.localdate()

    # Open tasks assigned to the person who is logged in,
    # leaving out projects that are completed or cancelled
    my_open_tasks = (
        visible_tasks(request)
        .filter(assignee=request.user, project__status__in=Project.ACTIVE_STATUSES)
        .exclude(status=Task.Status.DONE)
        .select_related("project")
    )

    context = {
        "active_projects": visible_projects(request).filter(status__in=Project.ACTIVE_STATUSES).count(),
        "open_tasks": my_open_tasks.count(),
        "overdue_tasks": my_open_tasks.filter(due_date__lt=today).count(),
        # The next 5 tasks by due date; tasks without a date come last
        "next_tasks": my_open_tasks.order_by(F("due_date").asc(nulls_last=True))[:5],
    }
    return render(request, "home.html", context)


def filter_projects(request):
    """The search box and status filter of the project list.
    The list page and its Excel export both use this, so they always show the same projects."""
    projects = visible_projects(request).with_task_counts().select_related("manager")

    # ?status=... in the address: "active" (the default), "all", or one status
    status = request.GET.get("status", "active")
    if status == "all":
        pass
    elif status in Project.Status.values:
        projects = projects.filter(status=status)
    else:
        status = "active"
        projects = projects.filter(status__in=Project.ACTIVE_STATUSES)

    # ?q=... in the address: part of a project name
    query = request.GET.get("q", "").strip()
    if query:
        projects = projects.filter(name__icontains=query)
    return projects, status, query


@login_required
def project_list(request):
    """The projects people can see, with a search box and a status filter."""
    projects, status, query = filter_projects(request)
    context = {
        "projects": projects,
        "status": status,
        "query": query,
        "status_choices": Project.Status.choices,
        # False when the person isn't in any project yet (and not because of the filters)
        "has_projects": visible_projects(request).exists(),
    }
    return render(request, "projects/project_list.html", context)


@login_required
def project_detail(request, pk):
    """One project: its details, progress, team, tasks and recent activity."""
    project = get_object_or_404(visible_projects(request).with_task_counts().select_related("manager"), pk=pk)
    # Main tasks only; each one brings along its subtasks and the tasks it waits for.
    # Sorted by due date: the order people choose by dragging only applies on the board.
    tasks = (
        project.tasks.filter(parent__isnull=True)
        .select_related("assignee")
        .order_by("due_date", "id")
        .prefetch_related(
            "depends_on",
            Prefetch(
                "subtasks",
                queryset=Task.objects.select_related("assignee")
                .prefetch_related("depends_on")
                .order_by("due_date", "id"),
            ),
        )
    )
    # The team, with each person's open tasks in this project (counted in one query)
    team = project.team()
    open_tasks = dict(
        project.tasks.exclude(status=Task.Status.DONE)
        .values_list("assignee")
        .annotate(count=Count("id"))
        .order_by()
    )
    for person in team:
        person.open_tasks = open_tasks.get(person.pk, 0)
    can_manage_team = project.can_manage_team(request.user)
    team_form = TeamMemberForm(project=project) if can_manage_team else None

    # The last 5 things that happened in this project (the Activity page shows everything)
    recent_activity, _older_than = get_events(viewer=request.user, project=project, limit=5)
    context = {
        "project": project,
        "tasks": tasks,
        "team": team,
        "can_manage_team": can_manage_team,
        "team_form": team_form,
        # False when everyone is in the team already
        "people_to_add": team_form is not None and team_form.fields["person"].queryset.exists(),
        "can_edit": project.can_edit(request.user),
        "recent_activity": recent_activity,
    }
    return render(request, "projects/project_detail.html", context)


@login_required
def project_create(request):
    if not request.user.is_admin:
        raise PermissionDenied(_("Only admins can create projects."))

    if request.method == "POST":
        form = ProjectForm(request.POST, user=request.user)
        if form.is_valid():
            project = form.save()
            messages.success(request, _('Project "%(name)s" created.') % {"name": project.name})
            return redirect(project)
    else:
        # Suggest the person creating the project as its manager
        form = ProjectForm(user=request.user, initial={"manager": request.user})

    return render(request, "projects/project_form.html", {"form": form})


@login_required
def project_edit(request, pk):
    project = get_object_or_404(visible_projects(request), pk=pk)
    if not project.can_edit(request.user):
        raise PermissionDenied(_("Only the project's manager and admins can edit this project."))

    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project, user=request.user)
        if form.is_valid():
            project.saved_by = request.user
            form.save()
            messages.success(request, _("Changes saved."))
            # A new manager: the previous one stays in the team, as a member (see Project.save)
            if project.kept_in_team:
                stays = _("%(person)s stays in the team, as a member.") % {"person": project.kept_in_team}
                messages.info(request, stays)
            return redirect(project)
    else:
        form = ProjectForm(instance=project, user=request.user)

    return render(request, "projects/project_form.html", {"form": form, "project": project})


@login_required
def project_delete(request, pk):
    """Only admins delete projects (like only admins create them). A page asks to type the project's
    name first; the project is deleted when that page is sent with the right name."""
    project = get_object_or_404(visible_projects(request), pk=pk)
    if not request.user.is_admin:
        raise PermissionDenied(_("Only admins can delete projects."))

    # The page to go back to (like Administration > Projects), given by the link as ?next=...
    # Only addresses on this site are accepted, so a link can't send people elsewhere.
    next_page = request.POST.get("next") or request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(next_page, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        next_page = ""

    if request.method == "POST":
        form = ProjectDeleteForm(request.POST, project=project)
        if form.is_valid():
            name = project.name
            # Its tasks, comments, files and team go with it. The history keeps a trace:
            # admins still see "deleted the project" on the Activity page.
            project.delete()
            messages.success(request, _('Project "%(name)s" deleted.') % {"name": name})
            return redirect(next_page or "project_list")
    else:
        form = ProjectDeleteForm(project=project)

    context = {
        "project": project,
        "form": form,
        "next": next_page,
        "task_count": project.tasks.count(),
        "comment_count": Comment.objects.filter(task__project=project).count(),
        "file_count": Attachment.objects.filter(task__project=project).count(),
        "team_count": len(project.team()),
    }
    return render(request, "projects/project_confirm_delete.html", context)


# ---------- The project's team ----------
# The project's manager and the admins add people to the team and remove them.


@login_required
@require_POST
def team_add(request, pk):
    project = get_object_or_404(visible_projects(request), pk=pk)
    if not project.can_manage_team(request.user):
        raise PermissionDenied(_("Only the project's manager and admins can change its team."))

    form = TeamMemberForm(request.POST, project=project)
    if form.is_valid():
        person = form.cleaned_data["person"]
        project.add_to_team(person, added_by=request.user)
        messages.success(request, _("%(person)s is now in the team.") % {"person": person})
    else:
        messages.error(request, " ".join(form.errors["person"]))
    # Back to the top of the project page, where the message is shown (the team is just below)
    return redirect(project)


@login_required
@require_POST
def team_remove(request, pk, user_pk):
    project = get_object_or_404(visible_projects(request), pk=pk)
    if not project.can_manage_team(request.user):
        raise PermissionDenied(_("Only the project's manager and admins can change its team."))
    membership = get_object_or_404(ProjectMember.objects.select_related("user"), project=project, user_id=user_pk)
    person = membership.user
    if person.pk == project.manager_id:
        messages.error(
            request, _("%(person)s is the project's manager: the manager is always in the team.") % {"person": person}
        )
        return redirect(project)

    # The person's open tasks must first go to someone else, so nobody is left with tasks
    # in a project they're not part of. Finished tasks don't count: they keep the person's name.
    open_count = project.open_tasks_of(person).count()
    if open_count:
        messages.error(
            request,
            ngettext(
                "%(person)s still has %(count)d open task in this project. "
                "Give it to someone else (or finish it), then remove %(person)s from the team.",
                "%(person)s still has %(count)d open tasks in this project. "
                "Give them to someone else (or finish them), then remove %(person)s from the team.",
                open_count,
            ) % {"person": person, "count": open_count},
        )
    else:
        membership.delete()  # recorded in the history, so the Activity page shows it
        messages.success(request, _("%(person)s is no longer in the team.") % {"person": person})
    return redirect(project)


# ---------- Tasks ----------


@login_required
def task_detail(request, pk):
    """One task: its details, status buttons, subtasks, dependencies, comments and files."""
    task = get_object_or_404(visible_tasks(request).select_related("project", "parent", "assignee"), pk=pk)

    # "task__project" comes along so can_delete() needs no extra database queries
    comments = list(task.comments.select_related("author", "task__project"))
    for comment in comments:
        comment.deletable = comment.can_delete(request.user)
    attachments = list(
        task.attachments.select_related("uploaded_by", "task__project").order_by("uploaded_at")
    )
    for attachment in attachments:
        attachment.deletable = attachment.can_delete(request.user)

    context = {
        "task": task,
        "subtasks": task.subtasks.select_related("assignee")
        .prefetch_related("depends_on")
        .order_by("due_date", "id"),
        "depends_on": task.depends_on.select_related("assignee"),
        "needed_by": task.blocking.select_related("assignee"),
        "status_choices": Task.Status.choices,
        "can_edit": task.can_edit(request.user),
        "can_update_status": task.can_update_status(request.user),
        "comments": comments,
        "attachments": attachments,
        "comment_form": CommentForm(),
        "attachment_form": AttachmentForm(),
        "max_upload_mb": MAX_UPLOAD_MB,
    }
    return render(request, "projects/task_detail.html", context)


def tell_new_assignee(request, task, previous_assignee_id):
    """After a task is saved: tell the person it was just given to (the bell, and an email).
    No email when the task was already theirs, or when people give a task to themselves."""
    if task.assignee_id in (None, previous_assignee_id, request.user.pk):
        return
    notify_assigned(task, assigned_by=request.user)  # the bell, whatever happens to the email
    if not email_new_assignee(task, assigned_by=request.user):
        messages.warning(
            request, _("The task is saved, but the email to %(person)s couldn't be sent.") % {"person": task.assignee}
        )


def tell_if_joined_team(request, task):
    """After a task is saved: say so if its person was just put in the project's team
    (when an admin gives a task to someone outside the team, or a finished task is opened again)."""
    if task.joined_team:
        messages.info(request, _("%(person)s is now in the team of this project.") % {"person": task.assignee})


@login_required
def task_create(request, project_pk):
    project = get_object_or_404(visible_projects(request), pk=project_pk)
    if not project.can_edit(request.user):
        raise PermissionDenied(_("Only the project's manager and admins can add tasks."))

    if request.method == "POST":
        form = TaskForm(request.POST, project=project, user=request.user)
        if form.is_valid():
            form.instance.saved_by = request.user
            task = form.save()
            messages.success(request, _('Task "%(title)s" created.') % {"title": task.title})
            tell_if_joined_team(request, task)
            tell_new_assignee(request, task, previous_assignee_id=None)
            return redirect(task)
    else:
        # "Add subtask" links end with ?parent=<number of the main task>
        form = TaskForm(project=project, user=request.user, initial={"parent": request.GET.get("parent")})

    return render(request, "projects/task_form.html", {"form": form, "project": project})


@login_required
def task_edit(request, pk):
    task = get_object_or_404(visible_tasks(request).select_related("project"), pk=pk)
    if not task.can_edit(request.user):
        raise PermissionDenied(_("Only the project's manager and admins can edit tasks."))

    if request.method == "POST":
        previous_assignee_id = task.assignee_id  # read it now: checking the form changes the task
        form = TaskForm(request.POST, instance=task, project=task.project, user=request.user)
        if form.is_valid():
            task.saved_by = request.user
            form.save()
            messages.success(request, _("Changes saved."))
            tell_if_joined_team(request, task)
            tell_new_assignee(request, task, previous_assignee_id)
            return redirect(task)
    else:
        form = TaskForm(instance=task, project=task.project, user=request.user)

    context = {"form": form, "project": task.project, "task": task}
    return render(request, "projects/task_form.html", context)


@login_required
@require_POST
def task_status(request, pk):
    """The status buttons on a task's page send their choice here."""
    task = get_object_or_404(visible_tasks(request).select_related("project"), pk=pk)
    if not task.can_update_status(request.user):
        raise PermissionDenied(
            _("Only the person assigned to this task, the project's manager and admins can change its status.")
        )

    new_status = request.POST.get("status")
    if new_status not in Task.Status.values:
        messages.error(request, _("Unknown status."))
    elif new_status != task.status:
        task.status = new_status
        task.saved_by = request.user
        task.save()
        messages.success(
            request, _('"%(title)s" is now %(status)s.') % {"title": task.title, "status": task.get_status_display()}
        )
        tell_if_joined_team(request, task)

    # A button can ask to come back to its own page (like My tasks) with a "next" field.
    # Only addresses on this site are accepted, so a link can't send people elsewhere.
    next_page = request.POST.get("next")
    if next_page and url_has_allowed_host_and_scheme(
        next_page, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_page)
    return redirect(task)


@login_required
def task_delete(request, pk):
    """Shows a confirmation page first; the task is only deleted when that page is sent."""
    task = get_object_or_404(visible_tasks(request).select_related("project"), pk=pk)
    if not task.can_edit(request.user):
        raise PermissionDenied(_("Only the project's manager and admins can delete tasks."))

    if request.method == "POST":
        project = task.project
        title = task.title
        task.delete()  # its subtasks, comments and files are deleted with it
        messages.success(request, _('Task "%(title)s" deleted.') % {"title": title})
        return redirect(project)

    return render(request, "projects/task_confirm_delete.html", {"task": task})


# ---------- Comments and files ----------
# Everyone who can see a task (its project's team and the admins) can comment on it and add files.


@login_required
@require_POST
def comment_add(request, task_pk):
    task = get_object_or_404(visible_tasks(request), pk=task_pk)
    form = CommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Write something before sending a comment."))
        return redirect(task)
    comment = form.save(commit=False)  # build the comment, but don't save it yet...
    comment.task = task  # ...because the task and the author come from the page, not the form
    comment.author = request.user
    comment.save()
    # "#comments" scrolls the page down to the comments
    return redirect(f"{task.get_absolute_url()}#comments")


@login_required
@require_POST
def comment_delete(request, pk):
    comment = get_object_or_404(
        Comment.objects.filter(task__in=visible_tasks(request)).select_related("task__project"), pk=pk
    )
    if not comment.can_delete(request.user):
        raise PermissionDenied(_("You can only delete your own comments."))
    comment.delete()
    return redirect(f"{comment.task.get_absolute_url()}#comments")


@login_required
@require_POST
def attachment_add(request, task_pk):
    task = get_object_or_404(visible_tasks(request), pk=task_pk)
    # Files arrive in request.FILES, separately from the other form fields
    form = AttachmentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, " ".join(form.errors["file"]))
        return redirect(task)
    attachment = form.save(commit=False)
    attachment.task = task
    attachment.uploaded_by = request.user
    attachment.save()
    return redirect(f"{task.get_absolute_url()}#files")


@login_required
@require_POST
def attachment_delete(request, pk):
    attachment = get_object_or_404(
        Attachment.objects.filter(task__in=visible_tasks(request)).select_related("task__project"), pk=pk
    )
    if not attachment.can_delete(request.user):
        raise PermissionDenied(_("You can only delete files you uploaded."))
    attachment.delete()  # the file on the disk is deleted too (see models.py)
    return redirect(f"{attachment.task.get_absolute_url()}#files")


@login_required
def attachment_download(request, path):
    """Every uploaded file is downloaded through here, so only people who can see the task get it.
    Only files that belong to an attachment are given out, nothing else from the disk."""
    attachment = get_object_or_404(Attachment.objects.filter(task__in=visible_tasks(request)), file=path)
    try:
        file = attachment.file.open("rb")
    except OSError:
        raise Http404(_("This file is missing from the server."))
    # as_attachment=True: the browser saves the file instead of opening it inside the app,
    # so an uploaded web page can't run inside our site
    return FileResponse(file, as_attachment=True, filename=attachment.filename)


# ---------- Kanban board ----------


@login_required
def project_board(request, pk):
    """The project's tasks as cards in four columns, one column per status."""
    project = get_object_or_404(visible_projects(request), pk=pk)
    # Cards keep the order people chose by dragging ("position"), then their due date
    tasks = project.tasks.select_related("assignee", "parent", "project").prefetch_related(
        "depends_on"
    )
    cards_by_status = {status: [] for status in Task.Status.values}
    for task in tasks:
        # Only the people who may change a task's status can drag its card
        task.movable = task.can_update_status(request.user)
        cards_by_status[task.status].append(task)

    context = {
        "project": project,
        "columns": [(status, label, cards_by_status[status]) for status, label in Task.Status.choices],
    }
    return render(request, "projects/project_board.html", context)


@login_required
@require_POST
def task_move(request, pk):
    """After a drag-and-drop, the board sends the card's new column (its status)
    and the new order of the cards in that column. The answer is JSON, not a page."""
    task = get_object_or_404(visible_tasks(request).select_related("project"), pk=pk)
    if not task.can_update_status(request.user):
        return JsonResponse({"error": _("You can only move tasks assigned to you.")}, status=403)
    new_status = request.POST.get("status")
    if new_status not in Task.Status.values:
        return JsonResponse({"error": _("Unknown status.")}, status=400)

    if new_status != task.status:
        task.status = new_status
        task.saved_by = request.user
        task.save()  # a status change is recorded in the history

    # Save each card's place in the column (0 = top). Only cards of this project and column count.
    order = [int(task_id) for task_id in request.POST.getlist("order") if task_id.isdigit()]
    places = {task_id: place for place, task_id in enumerate(order)}
    cards = list(Task.objects.filter(project=task.project, status=new_status, pk__in=places))
    for card in cards:
        card.position = places[card.pk]
    Task.objects.bulk_update(cards, ["position"])  # the order isn't recorded in the history

    # Tell the board which cards are late or waiting now, so it can update their look
    project_tasks = Task.objects.filter(project=task.project).order_by()
    late = project_tasks.filter(due_date__lt=timezone.localdate()).exclude(status=Task.Status.DONE)
    waiting = project_tasks.filter(depends_on__in=Task.objects.exclude(status=Task.Status.DONE))
    return JsonResponse({
        "overdue_ids": list(late.values_list("pk", flat=True)),
        "waiting_ids": list(waiting.values_list("pk", flat=True).distinct()),
    })


# ---------- Gantt chart (timeline) ----------


def gantt_words():
    """The words that the chart writes itself (always in English), in the language of the page.
    The keys are the English words; the script puts the translations on the chart's buttons."""
    # The first text of pgettext() tells translators what these short words are
    return {
        "Today": _("Today"),
        "Mode": pgettext("timeline: the list of zoom levels", "Mode"),
        "Hour": pgettext("timeline zoom level", "Hour"),
        "Quarter Day": pgettext("timeline zoom level", "Quarter Day"),
        "Half Day": pgettext("timeline zoom level", "Half Day"),
        "Day": pgettext("timeline zoom level", "Day"),
        "Week": pgettext("timeline zoom level", "Week"),
        "Month": pgettext("timeline zoom level", "Month"),
        "Year": pgettext("timeline zoom level", "Year"),
        "open_task": _("Open task"),
        "refused": _("The server refused the change."),
        "not_saved": _("The new dates weren't saved. {error} The page will reload."),
    }


@login_required
def project_gantt(request, pk):
    """The project's tasks as bars on a timeline, from their start date to their due date."""
    project = get_object_or_404(visible_projects(request), pk=pk)
    tasks = list(project.tasks.select_related("assignee").prefetch_related("depends_on"))

    # A task needs at least one date to be drawn; the others are listed under the chart
    dated = [task for task in tasks if task.start_date or task.due_date]
    undated = [task for task in tasks if not (task.start_date or task.due_date)]
    dated_ids = {task.pk for task in dated}

    def first_day(task):
        return task.start_date or task.due_date

    # Rows: main tasks by date, each one followed by its subtasks
    main_tasks = sorted((t for t in dated if t.parent_id is None or t.parent_id not in dated_ids),
                        key=lambda t: (first_day(t), t.pk))
    rows = []
    for main in main_tasks:
        rows.append(main)
        rows.extend(sorted((t for t in dated if t.parent_id == main.pk), key=lambda t: (first_day(t), t.pk)))

    chart_tasks = []
    for task in rows:
        start = task.start_date or task.due_date  # one date only: a one-day bar
        end = task.due_date or task.start_date
        prefix = "◆ " if task.is_milestone else ("↳ " if task.parent_id in dated_ids else "")
        chart_tasks.append({
            "id": str(task.pk),
            "name": prefix + task.title,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "progress": 0,
            # Arrows: only to tasks that are on the chart too
            "dependencies": ", ".join(str(d.pk) for d in task.depends_on.all() if d.pk in dated_ids),
            "custom_class": f"status-{task.status}",  # colors the bar (see the page's styles)
            "status": task.get_status_display(),
            "assignee": str(task.assignee or _("Unassigned")),
            # The arrow points to the end date: to the left in Arabic, which reads right to left
            "dates": _("%(start)s → %(end)s") % {
                "start": date_format(start, _("d M Y")), "end": date_format(end, _("d M Y")),
            },
            "url": task.get_absolute_url(),
            "dates_url": reverse("task_dates", args=[task.pk]),
        })

    today = timezone.localdate()
    spans_today = any(first_day(t) <= today <= (t.due_date or t.start_date) for t in dated)
    context = {
        "project": project,
        "chart_tasks": chart_tasks,
        "chart_words": gantt_words(),
        "undated": undated,
        "can_edit": project.can_edit(request.user),
        # Open the chart on today if a task is running now, otherwise on the first task
        "scroll_to": "today" if spans_today else "start",
    }
    return render(request, "projects/project_gantt.html", context)


@login_required
@require_POST
def task_dates(request, pk):
    """After a bar is dragged or stretched, the chart sends the task's new dates here."""
    task = get_object_or_404(visible_tasks(request).select_related("project", "parent"), pk=pk)
    if not task.can_edit(request.user):
        return JsonResponse({"error": _("Only the project's manager and admins can change dates.")}, status=403)
    try:
        task.start_date = date.fromisoformat(request.POST["start_date"])
        task.due_date = date.fromisoformat(request.POST["due_date"])
    except (KeyError, ValueError):
        return JsonResponse({"error": _("These dates aren't valid.")}, status=400)
    try:
        task.clean()  # the same rules as the task form, like "the due date can't be before the start"
    except ValidationError as error:
        return JsonResponse({"error": " ".join(error.messages)}, status=400)
    task.saved_by = request.user
    task.save()  # recorded in the history
    return JsonResponse({"start_date": task.start_date.isoformat(), "due_date": task.due_date.isoformat()})


# ---------- My tasks ----------


@login_required
def my_tasks(request):
    """Everything assigned to the person who is logged in, sorted by when it's due."""
    today = timezone.localdate()
    open_tasks = (
        visible_tasks(request)
        .filter(assignee=request.user, project__status__in=Project.ACTIVE_STATUSES)
        .exclude(status=Task.Status.DONE)
        .select_related("project", "parent")
        .prefetch_related("depends_on")
        .order_by("due_date", "id")
    )
    sections = {"overdue": [], "this_week": [], "later": [], "no_date": []}
    for task in open_tasks:
        if task.due_date is None:
            sections["no_date"].append(task)
        elif task.due_date < today:
            sections["overdue"].append(task)
        elif task.due_date <= today + timedelta(days=6):
            sections["this_week"].append(task)
        else:
            sections["later"].append(task)

    recently_done = (
        visible_tasks(request).filter(
            assignee=request.user,
            status=Task.Status.DONE,
            completed_at__gte=timezone.now() - timedelta(days=14),
        )
        .select_related("project")
        .order_by("-completed_at")[:10]
    )
    context = {
        "sections": [
            ("overdue", _("Overdue"), "ti-alert-triangle text-danger", sections["overdue"]),
            ("this_week", _("Due in the next 7 days"), "ti-calendar-event", sections["this_week"]),
            ("later", _("Later"), "ti-calendar", sections["later"]),
            ("no_date", _("No due date"), "ti-calendar-off", sections["no_date"]),
        ],
        "open_count": sum(len(tasks) for tasks in sections.values()),
        "recently_done": recently_done,
    }
    return render(request, "projects/my_tasks.html", context)


# ---------- Dashboard ----------


@login_required
def dashboard(request):
    """The big picture: numbers, workload, and what needs attention.
    Admins see all the projects; everyone else sees the projects they are part of."""
    today = timezone.localdate()
    projects = visible_projects(request)
    tasks = visible_tasks(request)
    active_projects = projects.filter(status__in=Project.ACTIVE_STATUSES)
    open_tasks = tasks.filter(project__status__in=Project.ACTIVE_STATUSES).exclude(status=Task.Status.DONE)
    overdue_tasks = open_tasks.filter(due_date__lt=today)

    # Workload: each person's open tasks, split into late and on time (one query)
    rows = (
        open_tasks.values("assignee")
        .annotate(total=Count("id"), late=Count("id", filter=Q(due_date__lt=today)))
        .order_by("-total")
    )
    names = {user.pk: str(user) for user in get_user_model().objects.filter(pk__in=[r["assignee"] for r in rows])}
    workload = [
        {"name": names[r["assignee"]], "on_time": r["total"] - r["late"], "late": r["late"]}
        for r in rows if r["assignee"]
    ]
    unassigned = [r for r in rows if not r["assignee"]]
    if unassigned:  # unassigned work goes last
        r = unassigned[0]
        workload.append({"name": _("Unassigned"), "on_time": r["total"] - r["late"], "late": r["late"]})

    # Tasks finished per week, for the last 8 weeks (weeks start on Monday)
    first_monday = today - timedelta(days=today.weekday(), weeks=7)
    finished = (
        tasks.filter(completed_at__date__gte=first_monday)
        .annotate(week=TruncWeek("completed_at"))
        .values("week")
        .annotate(count=Count("id"))
        .order_by()
    )
    per_week = {row["week"].date(): row["count"] for row in finished}
    weeks = [first_monday + timedelta(weeks=i) for i in range(8)]
    done_per_week = [{"week": date_format(week, _("d M")), "count": per_week.get(week, 0)} for week in weeks]

    # Active projects that are late or have overdue tasks, worst first
    needs_attention = [
        project
        for project in projects.with_task_counts()
        .filter(status__in=Project.ACTIVE_STATUSES)
        .select_related("manager")
        if project.is_overdue or project.task_overdue
    ]
    needs_attention.sort(key=lambda project: -project.task_overdue)

    oldest_overdue = list(overdue_tasks.select_related("project", "assignee").order_by("due_date", "id")[:10])
    for task in oldest_overdue:
        task.days_late = (today - task.due_date).days

    context = {
        # The headline numbers; "warn" adds a warning icon when the number isn't zero
        "kpis": [
            {"label": _("Active projects"), "value": active_projects.count()},
            {"label": _("Late projects"), "value": active_projects.filter(end_date__lt=today).count(), "warn": True},
            {"label": _("Open tasks"), "value": open_tasks.count()},
            {"label": _("Overdue tasks"), "value": overdue_tasks.count(), "warn": True},
            {"label": _("Done in the last 30 days"),
             "value": tasks.filter(completed_at__gte=timezone.now() - timedelta(days=30)).count()},
        ],
        "workload": workload,
        "done_per_week": done_per_week,
        "needs_attention": needs_attention,
        "oldest_overdue": oldest_overdue,
    }
    return render(request, "projects/dashboard.html", context)
