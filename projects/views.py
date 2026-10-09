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
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import MAX_UPLOAD_MB, AttachmentForm, CommentForm, ProjectForm, TaskForm
from .models import Attachment, Comment, Project, Task


@login_required
def home(request):
    """The first page people see after logging in."""
    today = timezone.localdate()

    # Open tasks assigned to the person who is logged in,
    # leaving out projects that are completed or cancelled
    my_open_tasks = (
        Task.objects.filter(assignee=request.user, project__status__in=Project.ACTIVE_STATUSES)
        .exclude(status=Task.Status.DONE)
        .select_related("project")
    )

    context = {
        "active_projects": Project.objects.filter(status__in=Project.ACTIVE_STATUSES).count(),
        "open_tasks": my_open_tasks.count(),
        "overdue_tasks": my_open_tasks.filter(due_date__lt=today).count(),
        # The next 5 tasks by due date; tasks without a date come last
        "next_tasks": my_open_tasks.order_by(F("due_date").asc(nulls_last=True))[:5],
    }
    return render(request, "home.html", context)


@login_required
def project_list(request):
    """All projects, with a search box and a status filter."""
    projects = Project.objects.with_task_counts().select_related("manager")

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

    context = {
        "projects": projects,
        "status": status,
        "query": query,
        "status_choices": Project.Status.choices,
    }
    return render(request, "projects/project_list.html", context)


@login_required
def project_detail(request, pk):
    """One project: its details, progress and tasks."""
    project = get_object_or_404(
        Project.objects.with_task_counts().select_related("manager"), pk=pk
    )
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
    context = {
        "project": project,
        "tasks": tasks,
        "can_edit": project.can_edit(request.user),
    }
    return render(request, "projects/project_detail.html", context)


@login_required
def project_create(request):
    if not request.user.can_manage:
        raise PermissionDenied("Only admins and managers can create projects.")

    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save()
            messages.success(request, f'Project "{project.name}" created.')
            return redirect(project)
    else:
        # Suggest the person creating the project as its manager
        form = ProjectForm(initial={"manager": request.user})

    return render(request, "projects/project_form.html", {"form": form})


@login_required
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not project.can_edit(request.user):
        raise PermissionDenied("Only admins and managers can edit projects.")

    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, "Changes saved.")
            return redirect(project)
    else:
        form = ProjectForm(instance=project)

    return render(request, "projects/project_form.html", {"form": form, "project": project})


# ---------- Tasks ----------


@login_required
def task_detail(request, pk):
    """One task: its details, status buttons, subtasks, dependencies, comments and files."""
    task = get_object_or_404(Task.objects.select_related("project", "parent", "assignee"), pk=pk)

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


@login_required
def task_create(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    if not project.can_edit(request.user):
        raise PermissionDenied("Only admins and managers can add tasks.")

    if request.method == "POST":
        form = TaskForm(request.POST, project=project)
        if form.is_valid():
            task = form.save()
            messages.success(request, f'Task "{task.title}" created.')
            return redirect(task)
    else:
        # "Add subtask" links end with ?parent=<number of the main task>
        form = TaskForm(project=project, initial={"parent": request.GET.get("parent")})

    return render(request, "projects/task_form.html", {"form": form, "project": project})


@login_required
def task_edit(request, pk):
    task = get_object_or_404(Task.objects.select_related("project"), pk=pk)
    if not task.can_edit(request.user):
        raise PermissionDenied("Only admins and managers can edit tasks.")

    if request.method == "POST":
        form = TaskForm(request.POST, instance=task, project=task.project)
        if form.is_valid():
            form.save()
            messages.success(request, "Changes saved.")
            return redirect(task)
    else:
        form = TaskForm(instance=task, project=task.project)

    context = {"form": form, "project": task.project, "task": task}
    return render(request, "projects/task_form.html", context)


@login_required
@require_POST
def task_status(request, pk):
    """The status buttons on a task's page send their choice here."""
    task = get_object_or_404(Task.objects.select_related("project"), pk=pk)
    if not task.can_update_status(request.user):
        raise PermissionDenied(
            "Only the person assigned to this task, managers and admins can change its status."
        )

    new_status = request.POST.get("status")
    if new_status not in Task.Status.values:
        messages.error(request, "Unknown status.")
    elif new_status != task.status:
        task.status = new_status
        task.save()
        messages.success(request, f'"{task.title}" is now {task.get_status_display()}.')

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
    task = get_object_or_404(Task.objects.select_related("project"), pk=pk)
    if not task.can_edit(request.user):
        raise PermissionDenied("Only admins and managers can delete tasks.")

    if request.method == "POST":
        project = task.project
        title = task.title
        task.delete()  # its subtasks, comments and files are deleted with it
        messages.success(request, f'Task "{title}" deleted.')
        return redirect(project)

    return render(request, "projects/task_confirm_delete.html", {"task": task})


# ---------- Comments and files ----------
# Everyone who is logged in can comment on any task and add files to it.


@login_required
@require_POST
def comment_add(request, task_pk):
    task = get_object_or_404(Task, pk=task_pk)
    form = CommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Write something before sending a comment.")
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
    comment = get_object_or_404(Comment.objects.select_related("task__project"), pk=pk)
    if not comment.can_delete(request.user):
        raise PermissionDenied("You can only delete your own comments.")
    comment.delete()
    return redirect(f"{comment.task.get_absolute_url()}#comments")


@login_required
@require_POST
def attachment_add(request, task_pk):
    task = get_object_or_404(Task, pk=task_pk)
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
    attachment = get_object_or_404(Attachment.objects.select_related("task__project"), pk=pk)
    if not attachment.can_delete(request.user):
        raise PermissionDenied("You can only delete files you uploaded.")
    attachment.delete()  # the file on the disk is deleted too (see models.py)
    return redirect(f"{attachment.task.get_absolute_url()}#files")


@login_required
def attachment_download(request, path):
    """Every uploaded file is downloaded through here, so only logged-in people can get it.
    Only files that belong to an attachment are given out, nothing else from the disk."""
    attachment = get_object_or_404(Attachment, file=path)
    try:
        file = attachment.file.open("rb")
    except OSError:
        raise Http404("This file is missing from the server.")
    # as_attachment=True: the browser saves the file instead of opening it inside the app,
    # so an uploaded web page can't run inside our site
    return FileResponse(file, as_attachment=True, filename=attachment.filename)


# ---------- Kanban board ----------


@login_required
def project_board(request, pk):
    """The project's tasks as cards in four columns, one column per status."""
    project = get_object_or_404(Project, pk=pk)
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
    task = get_object_or_404(Task.objects.select_related("project"), pk=pk)
    if not task.can_update_status(request.user):
        return JsonResponse({"error": "You can only move tasks assigned to you."}, status=403)
    new_status = request.POST.get("status")
    if new_status not in Task.Status.values:
        return JsonResponse({"error": "Unknown status."}, status=400)

    if new_status != task.status:
        task.status = new_status
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


@login_required
def project_gantt(request, pk):
    """The project's tasks as bars on a timeline, from their start date to their due date."""
    project = get_object_or_404(Project, pk=pk)
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
            "assignee": str(task.assignee or "Unassigned"),
            "dates": f"{start:%d %b %Y} → {end:%d %b %Y}",
            "url": task.get_absolute_url(),
            "dates_url": reverse("task_dates", args=[task.pk]),
        })

    today = timezone.localdate()
    spans_today = any(first_day(t) <= today <= (t.due_date or t.start_date) for t in dated)
    context = {
        "project": project,
        "chart_tasks": chart_tasks,
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
    task = get_object_or_404(Task.objects.select_related("project", "parent"), pk=pk)
    if not task.can_edit(request.user):
        return JsonResponse({"error": "Only admins and managers can change dates."}, status=403)
    try:
        task.start_date = date.fromisoformat(request.POST["start_date"])
        task.due_date = date.fromisoformat(request.POST["due_date"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "These dates aren't valid."}, status=400)
    try:
        task.clean()  # the same rules as the task form, like "the due date can't be before the start"
    except ValidationError as error:
        return JsonResponse({"error": " ".join(error.messages)}, status=400)
    task.save()  # recorded in the history
    return JsonResponse({"start_date": task.start_date.isoformat(), "due_date": task.due_date.isoformat()})


# ---------- My tasks ----------


@login_required
def my_tasks(request):
    """Everything assigned to the person who is logged in, sorted by when it's due."""
    today = timezone.localdate()
    open_tasks = (
        Task.objects.filter(assignee=request.user, project__status__in=Project.ACTIVE_STATUSES)
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
        Task.objects.filter(
            assignee=request.user,
            status=Task.Status.DONE,
            completed_at__gte=timezone.now() - timedelta(days=14),
        )
        .select_related("project")
        .order_by("-completed_at")[:10]
    )
    context = {
        "sections": [
            ("overdue", "Overdue", "ti-alert-triangle text-danger", sections["overdue"]),
            ("this_week", "Due in the next 7 days", "ti-calendar-event", sections["this_week"]),
            ("later", "Later", "ti-calendar", sections["later"]),
            ("no_date", "No due date", "ti-calendar-off", sections["no_date"]),
        ],
        "open_count": sum(len(tasks) for tasks in sections.values()),
        "recently_done": recently_done,
    }
    return render(request, "projects/my_tasks.html", context)


# ---------- Dashboard for managers ----------


@login_required
def dashboard(request):
    """The big picture for admins and managers: numbers, workload, and what needs attention."""
    if not request.user.can_manage:
        raise PermissionDenied("Only admins and managers can see the dashboard.")

    today = timezone.localdate()
    active_projects = Project.objects.filter(status__in=Project.ACTIVE_STATUSES)
    open_tasks = Task.objects.filter(project__status__in=Project.ACTIVE_STATUSES).exclude(
        status=Task.Status.DONE
    )
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
        workload.append({"name": "Unassigned", "on_time": r["total"] - r["late"], "late": r["late"]})

    # Tasks finished per week, for the last 8 weeks (weeks start on Monday)
    first_monday = today - timedelta(days=today.weekday(), weeks=7)
    finished = (
        Task.objects.filter(completed_at__date__gte=first_monday)
        .annotate(week=TruncWeek("completed_at"))
        .values("week")
        .annotate(count=Count("id"))
        .order_by()
    )
    per_week = {row["week"].date(): row["count"] for row in finished}
    weeks = [first_monday + timedelta(weeks=i) for i in range(8)]
    done_per_week = [{"week": f"{week:%d %b}", "count": per_week.get(week, 0)} for week in weeks]

    # Active projects that are late or have overdue tasks, worst first
    needs_attention = [
        project
        for project in Project.objects.with_task_counts()
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
            {"label": "Active projects", "value": active_projects.count()},
            {"label": "Late projects", "value": active_projects.filter(end_date__lt=today).count(), "warn": True},
            {"label": "Open tasks", "value": open_tasks.count()},
            {"label": "Overdue tasks", "value": overdue_tasks.count(), "warn": True},
            {"label": "Done in the last 30 days",
             "value": Task.objects.filter(completed_at__gte=timezone.now() - timedelta(days=30)).count()},
        ],
        "workload": workload,
        "done_per_week": done_per_week,
        "needs_attention": needs_attention,
        "oldest_overdue": oldest_overdue,
    }
    return render(request, "projects/dashboard.html", context)
