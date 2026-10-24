"""Stage 19: notifications inside the app (the bell in the top bar)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .emails import email_project_event
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


def notify_project(kind, project, person, actor):
    """Something happened to this person in a project (added to the team, made manager...): leave a
    notification for them. Nothing for the person who did it to themselves."""
    if actor is not None and actor.pk == person.pk:
        return None
    return Notification.objects.create(recipient=person, kind=kind, project=project, actor=actor)


def tell_project_event(request, kind, project, person):
    """Someone's place in a project changed (added to the team, made manager...): tell them, in the bell
    and by email. Nothing for people who did it to themselves or whose account is deactivated.
    The change itself is already saved: a mail server that is down only shows a warning."""
    if not person.is_active or person.pk == request.user.pk:
        return
    notify_project(kind, project, person, request.user)  # the bell, whatever happens to the email
    if not email_project_event(kind, project, person, request.user):
        messages.warning(
            request, _("The change is saved, but the email to %(person)s couldn't be sent.") % {"person": person}
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
        Notification.for_person(request.user).select_related("task__project", "project", "actor")[:SHOWN]
    )
    return render(request, "projects/notifications.html", {"notifications": notifications})


@login_required
def notification_open(request, pk):
    """A click on a notification: it is marked as read, and the task (or the project) opens. Other people's
    notifications don't exist for you ("Page not found")."""
    notification = get_object_or_404(Notification.for_person(request.user).select_related("task", "project"), pk=pk)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return redirect(notification.target)


@login_required
@require_POST
def notification_read_all(request):
    Notification.for_person(request.user).filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notification_list")
