from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import F, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ProjectForm
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
    # Main tasks only; each one brings its subtasks along (2 queries in total)
    tasks = (
        project.tasks.filter(parent__isnull=True)
        .select_related("assignee")
        .prefetch_related(Prefetch("subtasks", queryset=Task.objects.select_related("assignee")))
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
