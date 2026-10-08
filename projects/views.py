from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import F, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ProjectForm, TaskForm
from .models import Project, Task


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
    # Main tasks only; each one brings along its subtasks and the tasks it waits for
    tasks = (
        project.tasks.filter(parent__isnull=True)
        .select_related("assignee")
        .prefetch_related(
            "depends_on",
            Prefetch(
                "subtasks",
                queryset=Task.objects.select_related("assignee").prefetch_related("depends_on"),
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
    """One task: its details, status buttons, subtasks and dependencies."""
    task = get_object_or_404(Task.objects.select_related("project", "parent", "assignee"), pk=pk)
    context = {
        "task": task,
        "subtasks": task.subtasks.select_related("assignee").prefetch_related("depends_on"),
        "depends_on": task.depends_on.select_related("assignee"),
        "needed_by": task.blocking.select_related("assignee"),
        "status_choices": Task.Status.choices,
        "can_edit": task.can_edit(request.user),
        "can_update_status": task.can_update_status(request.user),
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
