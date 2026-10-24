"""Emails: the daily reminder, and the email someone gets when a task is assigned to them.

While developing, settings.py prints the emails in the terminal instead of sending them
(the "console" email backend). On the server, the real mail server goes in .env (Step 15).
Each email has two versions, made from two templates in templates/emails/:
a plain-text one (.txt) and a nicer HTML one (.html). Email programs show the one they can.

Each email is written in the language saved in the profile of the person who gets it (Stage 17c),
whatever the language of the person who caused it. Someone who hasn't chosen a language yet gets the
language in settings.EMAIL_DEFAULT_LANGUAGE (French unless changed).
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext, pgettext

from .models import Notification, Project, ProjectMember, Task

# Problems (like a mail server that is down) are written in the server's log
logger = logging.getLogger(__name__)

DUE_SOON_DAYS = 3  # "due soon" = due today, tomorrow or the day after
MAX_PER_SECTION = 20  # longer lists end with "...and 5 more"


# ---------- Sending ----------


def email_language(person):
    """The language of the emails this person gets: the one in their profile; when they haven't chosen one
    yet (or it is unknown), settings.EMAIL_DEFAULT_LANGUAGE."""
    if person.language in dict(settings.LANGUAGES):
        return person.language
    if settings.EMAIL_DEFAULT_LANGUAGE in dict(settings.LANGUAGES):
        return settings.EMAIL_DEFAULT_LANGUAGE
    return person.get_language()


def render_email(template, context, language=None, site_url=None):
    """The plain-text and HTML versions of an email: emails/<template>.txt and .html,
    written in the given language (the language of the person who gets the email).
    site_url: the address the links start with (SITE_URL from settings.py unless told otherwise)."""
    context = {**context, "site_url": site_url or settings.SITE_URL}
    with translation.override(language or translation.get_language()):
        text = render_to_string(f"emails/{template}.txt", context)
        html = render_to_string(f"emails/{template}.html", context)
    return text, html


def send_email(to, subject, template, context, language=None):
    """Sends one email. If the mail server says no, this raises an error."""
    subject = " ".join(str(subject).split())  # an email subject must be on one line
    text, html = render_email(template, {**context, "subject": subject}, language)
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
    show_assignee: bool = False  # for project managers: who the task is assigned to

    @property
    def more(self):
        return self.count - len(self.tasks)


def describe_due_date(task, today):
    """Like "4 days late", "due today", "due tomorrow" or "due Sun 11 Oct"."""
    days = (task.due_date - today).days
    if days < 0:
        return ngettext("%(count)d day late", "%(count)d days late", -days) % {"count": -days}
    if days == 0:
        return _("due today")
    if days == 1:
        return _("due tomorrow")
    return _("due %(date)s") % {"date": date_format(task.due_date, _("D j M"))}


def reminder_for(person, today):
    """Everything the daily reminder tells this person, ready for the email templates.
    "sections" is empty when there is nothing to remind them of.
    The texts are written in the language of the person."""
    with translation.override(email_language(person)):
        return _reminder_for(person, today)


def _reminder_for(person, today):
    open_tasks = (
        Task.objects.visible_to(person)  # never a task the person can't open
        .filter(project__status__in=Project.ACTIVE_STATUSES, due_date__isnull=False)
        .exclude(status=Task.Status.DONE)
        .select_related("project", "assignee")
        .order_by("due_date", "id")
    )
    mine = open_tasks.filter(assignee=person)
    overdue = list(mine.filter(due_date__lt=today))
    due_soon = list(mine.filter(due_date__gte=today, due_date__lt=today + timedelta(days=DUE_SOON_DAYS)))
    # For project managers: late tasks in their projects that are someone else's (or nobody's)
    late_in_my_projects = list(
        open_tasks.filter(
            Q(project__manager=person)
            | Q(project__in=ProjectMember.objects.filter(user=person, is_manager=True).values("project")),
            due_date__lt=today,
        ).exclude(assignee=person)
    )

    sections = []
    summary = []  # for the subject, like "2 overdue, 1 due soon"
    for title, tasks, late, show_assignee, short in [
        (_("Overdue"), overdue, True, False, _("%(count)d overdue")),
        (
            ngettext("Due in the next %(days)d day", "Due in the next %(days)d days", DUE_SOON_DAYS)
            % {"days": DUE_SOON_DAYS},
            due_soon, False, False, _("%(count)d due soon"),
        ),
        (_("Late in projects you manage"), late_in_my_projects, True, True, _("%(count)d late in your projects")),
    ]:
        if not tasks:
            continue
        for task in tasks:
            task.due_text = describe_due_date(task, today)
        sections.append(Section(title, tasks[:MAX_PER_SECTION], len(tasks), late, show_assignee))
        summary.append(short % {"count": len(tasks)})

    separator = pgettext("separator between items of a list", ", ")
    return {
        "person": person,
        "today": today,
        "manages_projects": person.managed_projects.exists(),  # for the last line of the email
        "sections": sections,
        "subject": (
            _("Your tasks: %(summary)s") % {"summary": separator.join(summary)}
            if summary
            else _("Your tasks: nothing urgent")
        ),
    }


@login_required
def reminder_preview(request):
    """Shows your reminder email as it would look today, to check its design.
    ?text=1 shows the plain-text version. Admins can add ?user=<number> to see someone else's."""
    person = request.user
    if request.user.is_admin and request.GET.get("user", "").isdigit():
        person = get_object_or_404(get_user_model(), pk=request.GET["user"])
    reminder = reminder_for(person, timezone.localdate())
    # Shown the way the person will get it: in their language. The links start with the address this page was
    # opened at, not with SITE_URL: "127.0.0.1" and "localhost" are two different sites for a browser (each
    # keeps its own login), so a link to the other one would show the login page. The real emails use SITE_URL.
    here = f"{request.scheme}://{request.get_host()}"
    text, html = render_email("reminder", reminder, email_language(person), site_url=here)
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
    language = email_language(person)
    try:
        with translation.override(language):
            subject = _("New task for you: %(title)s") % {"title": task.title}
        send_email(
            person.email,
            subject,
            "assigned",
            {"task": task, "person": person, "assigned_by": assigned_by},
            language,
        )
    except Exception:  # like a mail server that is down or refuses the email
        logger.exception("Couldn't send the new task email to %s", person.email)
        return False
    return True


# ---------- Changes in a project's team or roles (Step 23) ----------


def project_event_texts(kind, project, actor):
    """The subject, the sentence and the explanation of a project email, in the current language.
    Call it inside translation.override(...)."""
    names = {"actor": actor or _("An administrator"), "project": project.name}
    if kind == Notification.Kind.ADDED_TO_TEAM:
        return (
            _("You were added to the project: %(project)s") % names,
            _("%(actor)s added you to the team of the project “%(project)s”.") % names,
            _("You can now see this project and its tasks."),
        )
    if kind == Notification.Kind.MANAGER:
        return (
            _("You are now the manager of: %(project)s") % names,
            _("%(actor)s made you the manager of the project “%(project)s”.") % names,
            _("You can edit the project, its tasks and its team."),
        )
    if kind == Notification.Kind.CO_MANAGER:
        return (
            _("You were given the manager role in: %(project)s") % names,
            _("%(actor)s gave you the manager role in the project “%(project)s”.") % names,
            _("You can edit the project, its tasks and its team, like its manager."),
        )
    if kind == Notification.Kind.MANAGER_ENDED:
        return (
            _("You are no longer the manager of: %(project)s") % names,
            _("%(actor)s chose another manager for the project “%(project)s”.") % names,
            _("You stay in the team, as a member."),
        )
    return (  # CO_MANAGER_ENDED
        _("Your manager role was taken away in: %(project)s") % names,
        _("%(actor)s took away your manager role in the project “%(project)s”.") % names,
        _("You stay in the team, as a member."),
    )


def email_project_event(kind, project, person, actor):
    """Tells someone by email that their place in a project changed.
    Returns False if the email couldn't be sent (the change is saved anyway)."""
    if not person.email:
        return True  # nobody to write to, but nothing went wrong
    language = email_language(person)
    try:
        with translation.override(language):
            subject, headline, detail = project_event_texts(kind, project, actor)
        send_email(
            person.email,
            subject,
            "project_event",
            {"project": project, "person": person, "headline": headline, "detail": detail},
            language,
        )
    except Exception:  # like a mail server that is down or refuses the email
        logger.exception("Couldn't send the project email (%s) to %s", kind, person.email)
        return False
    return True


# ---------- Passwords (Step 24) ----------


def email_password_reset(person, link):
    """The "forgot my password" email, with the one-time link. Raises an error if it can't be sent."""
    language = email_language(person)
    with translation.override(language):
        subject = _("Reset your password")
    send_email(person.email, subject, "password_reset", {"person": person, "link": link}, language)


def email_password_changed(person):
    """Tells someone that their password was just changed, so that they notice if it wasn't them.
    Returns False if the email couldn't be sent (the new password is saved anyway)."""
    if not person.email:
        return True
    language = email_language(person)
    try:
        with translation.override(language):
            subject = _("Your password was changed")
        send_email(person.email, subject, "password_changed", {"person": person}, language)
    except Exception:  # like a mail server that is down or refuses the email
        logger.exception("Couldn't send the password changed email to %s", person.email)
        return False
    return True


# ---------- A task you were waiting for is done (Step 24) ----------


def email_unblocked(task, finished, finished_by):
    """Tells the person assigned to `task` that `finished` (the last task it waited for) is done, so they
    can start. Returns False if the email couldn't be sent (the bell entry is saved anyway)."""
    person = task.assignee
    if not person.email:
        return True
    language = email_language(person)
    try:
        with translation.override(language):
            subject = _("You can start: %(title)s") % {"title": task.title}
        send_email(
            person.email,
            subject,
            "unblocked",
            {"task": task, "finished": finished, "finished_by": finished_by, "person": person},
            language,
        )
    except Exception:  # like a mail server that is down or refuses the email
        logger.exception("Couldn't send the unblocked task email to %s", person.email)
        return False
    return True
