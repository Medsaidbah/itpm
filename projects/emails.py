"""Emails: the daily reminder, and the email someone gets when a task is assigned to them.

While developing, settings.py prints the emails in the terminal instead of sending them
(the "console" email backend). On the server, the real mail server goes in .env (Step 15).
Each email has two versions, made from two templates in templates/emails/:
a plain-text one (.txt) and a nicer HTML one (.html). Email programs show the one they can.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format

from .models import Project, Task

# Problems (like a mail server that is down) are written in the server's log
logger = logging.getLogger(__name__)

DUE_SOON_DAYS = 3  # "due soon" = due today, tomorrow or the day after
MAX_PER_SECTION = 20  # longer lists end with "...and 5 more"


# ---------- Sending ----------


def render_email(template, context):
    """The plain-text and HTML versions of an email: emails/<template>.txt and .html"""
    context = {**context, "site_url": settings.SITE_URL}
    text = render_to_string(f"emails/{template}.txt", context)
    html = render_to_string(f"emails/{template}.html", context)
    return text, html


def send_email(to, subject, template, context):
    """Sends one email. If the mail server says no, this raises an error."""
    subject = " ".join(subject.split())  # an email subject must be on one line
    text, html = render_email(template, {**context, "subject": subject})
    # The sender is DEFAULT_FROM_EMAIL from settings.py
    message = EmailMultiAlternatives(subject, text, to=[to])
    message.attach_alternative(html, "text/html")
    message.send()


# ---------- The daily reminder ----------


@dataclass
class Section:
    """One list in the reminder, like "Overdue (2)"."""

    title: str
    tasks: list  # the tasks shown (at most MAX_PER_SECTION)
    count: int  # how many there are in total
    late: bool = False  # shown in red
    show_assignee: bool = False  # for managers: who the task is assigned to

    @property
    def more(self):
        return self.count - len(self.tasks)


def describe_due_date(task, today):
    """Like "4 days late", "due today", "due tomorrow" or "due Sun 11 Oct"."""
    days = (task.due_date - today).days
    if days < 0:
        return f"{-days} day{'s' if days < -1 else ''} late"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due {date_format(task.due_date, 'D j M')}"


def reminder_for(person, today):
    """Everything the daily reminder tells this person, ready for the email templates.
    "sections" is empty when there is nothing to remind them of."""
    open_tasks = (
        Task.objects.filter(project__status__in=Project.ACTIVE_STATUSES, due_date__isnull=False)
        .exclude(status=Task.Status.DONE)
        .select_related("project", "assignee")
        .order_by("due_date", "id")
    )
    mine = open_tasks.filter(assignee=person)
    overdue = list(mine.filter(due_date__lt=today))
    due_soon = list(mine.filter(due_date__gte=today, due_date__lt=today + timedelta(days=DUE_SOON_DAYS)))
    # For managers: late tasks in their projects that are someone else's (or nobody's)
    late_in_my_projects = list(
        open_tasks.filter(project__manager=person, due_date__lt=today).exclude(assignee=person)
    )

    sections = []
    summary = []  # for the subject, like "2 overdue, 1 due soon"
    for title, tasks, late, show_assignee, short in [
        ("Overdue", overdue, True, False, "overdue"),
        (f"Due in the next {DUE_SOON_DAYS} days", due_soon, False, False, "due soon"),
        ("Late in projects you manage", late_in_my_projects, True, True, "late in your projects"),
    ]:
        if not tasks:
            continue
        for task in tasks:
            task.due_text = describe_due_date(task, today)
        sections.append(Section(title, tasks[:MAX_PER_SECTION], len(tasks), late, show_assignee))
        summary.append(f"{len(tasks)} {short}")

    return {
        "person": person,
        "today": today,
        "sections": sections,
        "subject": ("Your tasks: " + ", ".join(summary)) if summary else "Your tasks: nothing urgent",
    }


@login_required
def reminder_preview(request):
    """Shows your reminder email as it would look today, to check its design.
    ?text=1 shows the plain-text version. Admins and managers can add ?user=<number>."""
    person = request.user
    if request.user.can_manage and request.GET.get("user", "").isdigit():
        person = get_object_or_404(get_user_model(), pk=request.GET["user"])
    reminder = reminder_for(person, timezone.localdate())
    text, html = render_email("reminder", reminder)
    if request.GET.get("text"):
        return HttpResponse(text, content_type="text/plain; charset=utf-8")
    return HttpResponse(html)


# ---------- "You have a new task" ----------


def email_new_assignee(task, assigned_by):
    """Tells someone by email that a task was just given to them.
    Returns False if the email couldn't be sent (the task is saved anyway)."""
    person = task.assignee
    if not person.email:
        return True  # no address: there is nobody to write to, but nothing went wrong
    try:
        send_email(
            person.email,
            f"New task for you: {task.title}",
            "assigned",
            {"task": task, "person": person, "assigned_by": assigned_by},
        )
    except Exception:  # like a mail server that is down or refuses the email
        logger.exception("Couldn't send the new task email to %s", person.email)
        return False
    return True
