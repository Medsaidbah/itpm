from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import render
from django.utils import timezone

from .models import Project, Task


@login_required
def home(request):
    """The first page people see after logging in."""
    today = timezone.localdate()

    # Open tasks assigned to the person who is logged in
    my_open_tasks = (
        Task.objects.filter(assignee=request.user)
        .exclude(status=Task.Status.DONE)
        .select_related("project")
    )

    context = {
        "active_projects": Project.objects.filter(
            status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD]
        ).count(),
        "open_tasks": my_open_tasks.count(),
        "overdue_tasks": my_open_tasks.filter(due_date__lt=today).count(),
        # The next 5 tasks by due date; tasks without a date come last
        "next_tasks": my_open_tasks.order_by(F("due_date").asc(nulls_last=True))[:5],
    }
    return render(request, "home.html", context)