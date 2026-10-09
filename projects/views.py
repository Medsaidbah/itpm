from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import F, Prefetch
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
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
        messages.success(request, f"Status changed to {task.get_status_display()}.")
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
