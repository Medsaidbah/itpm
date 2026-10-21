"""Stage 19: notifications inside the app (the bell in the top bar)."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification

SHOWN = 50  # the list shows the latest ones


def notify_assigned(task, assigned_by):
    """A task was given to someone else: leave a notification for them. Same rule as the email:
    nothing when the task has nobody, or when people give a task to themselves."""
    if task.assignee_id is None or task.assignee_id == assigned_by.pk:
        return None
    return Notification.objects.create(
        recipient=task.assignee, kind=Notification.Kind.ASSIGNED, task=task, actor=assigned_by
    )


def unread_count(request):
    """For every page: the number on the bell (a context processor, see settings.py)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"unread_notifications": Notification.for_person(user).filter(read_at__isnull=True).count()}


@login_required
def notification_list(request):
    notifications = list(
        Notification.for_person(request.user).select_related("task__project", "actor")[:SHOWN]
    )
    return render(request, "projects/notifications.html", {"notifications": notifications})


@login_required
def notification_open(request, pk):
    """A click on a notification: it is marked as read, and the task opens. Other people's
    notifications don't exist for you ("Page not found")."""
    notification = get_object_or_404(Notification.for_person(request.user).select_related("task"), pk=pk)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return redirect(notification.task)


@login_required
@require_POST
def notification_read_all(request):
    Notification.for_person(request.user).filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notification_list")
