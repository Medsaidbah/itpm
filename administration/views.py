"""The pages of the Administration section. Only admins can open them: everyone else gets
"No access" (403), even when typing the address by hand."""

from collections import Counter
from functools import wraps

from axes.helpers import get_cool_off, get_failure_limit
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, F, Q, Value
from django.db.models.functions import Concat
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from accounts.models import NO_ADMIN_LEFT, Feedback, User, leaves_no_active_admin
from projects.models import Notification, Project, ProjectMember, Task
from projects.notifications import tell_project_event

from .forms import (
    AccountForm,
    AddToProjectForm,
    ImportForm,
    NewAccountForm,
    NewPasswordForm,
    UnlockForm,
)
from .logins import failed_logins, unlock
from .passwords import free_username, read_people, temporary_password


def admin_required(view):
    """For every page of this section: you must be logged in, and be an admin."""

    @wraps(view)
    def check(request, *args, **kwargs):
        if not request.user.is_admin:
            raise PermissionDenied(_("Only admins can open the Administration section."))
        return view(request, *args, **kwargs)

    # Someone who isn't logged in is sent to the login page first
    return login_required(check)


def missing_details():
    """The accounts without a first name, a last name or an email (all three are needed since Stage 17a)."""
    return Q(first_name="") | Q(last_name="") | Q(email="")


# ---------- Overview ----------


@admin_required
def home(request):
    """The first page of the section: a few numbers, and where to go from there."""
    active = User.objects.filter(is_active=True)
    open_tasks = Task.objects.exclude(status=Task.Status.DONE)
    context = {
        "section": "home",
        "open_tasks": open_tasks.count(),
        "late_tasks": open_tasks.filter(due_date__lt=timezone.localdate()).count(),
        "active_count": active.count(),
        "deactivated_count": User.objects.filter(is_active=False).count(),
        "admin_count": active.filter(role=User.Role.ADMIN).count(),
        "missing_count": active.filter(missing_details()).count(),
        "locked_count": sum(1 for group in failed_logins(request) if group.locked),
        "active_projects": Project.objects.filter(status__in=Project.ACTIVE_STATUSES).count(),
        "finished_projects": Project.objects.exclude(status__in=Project.ACTIVE_STATUSES).count(),
        "feedback_total": Feedback.objects.count(),
        "feedback_new": Feedback.objects.filter(reviewed_at__isnull=True).count(),
        "feedback_average": Feedback.objects.aggregate(average=Avg("rating"))["average"],
    }
    return render(request, "administration/home.html", context)


# ---------- Users ----------


@admin_required
def user_list(request):
    """Every account, with a search box and filters (in the address: ?q=...&status=...&role=...)."""
    people = User.objects.order_by("first_name", "last_name", "username")

    status = request.GET.get("status", "active")  # active (the default), deactivated or all
    if status == "deactivated":
        people = people.filter(is_active=False)
    elif status != "all":
        status = "active"
        people = people.filter(is_active=True)

    role = request.GET.get("role", "")
    if role in User.Role.values:
        people = people.filter(role=role)
    else:
        role = ""

    missing = request.GET.get("missing") == "1"
    if missing:
        people = people.filter(missing_details())

    query = request.GET.get("q", "").strip()
    if query:
        # "Sara Ben" finds Sara Ben: the first and last names are searched together too
        people = people.annotate(full_name=Concat("first_name", Value(" "), "last_name")).filter(
            Q(username__icontains=query) | Q(full_name__icontains=query)
            | Q(email__icontains=query) | Q(phone__icontains=query)
        )

    # Each person's projects and open tasks, counted in three queries however many people there are
    managed = dict(Project.objects.values_list("manager").annotate(count=Count("id")).order_by())
    member = dict(
        ProjectMember.objects.exclude(project__manager=F("user"))  # a manager's old member line doesn't count
        .values_list("user").annotate(count=Count("id")).order_by()
    )
    open_tasks = dict(
        Task.objects.exclude(status=Task.Status.DONE).filter(assignee__isnull=False)
        .values_list("assignee").annotate(count=Count("id")).order_by()
    )
    locked = {group.username for group in failed_logins(request) if group.locked}
    people = list(people)
    for person in people:
        person.project_count = managed.get(person.pk, 0) + member.get(person.pk, 0)
        person.open_task_count = open_tasks.get(person.pk, 0)
        person.is_locked = person.username in locked
        person.has_missing_details = not (person.first_name and person.last_name and person.email)

    context = {
        "section": "users",
        "people": people,
        "query": query,
        "status": status,
        "role": role,
        "missing": missing,
        "role_choices": User.Role.choices,
        "filtered": bool(query or role or missing or status != "active"),
    }
    return render(request, "administration/user_list.html", context)


@admin_required
def user_create(request):
    if request.method == "POST":
        form = NewAccountForm(request.POST, request.FILES)
        if form.is_valid():
            person = form.save()
            # The username and the temporary password were made by the app: shown once, on this page only
            return passwords_sheet(request, [(person, form.temporary_password)])
    else:
        form = NewAccountForm()
    return render(request, "administration/user_new.html", {"section": "users", "form": form})


@admin_required
def user_detail(request, pk):
    """Everything about one account: its details (changed here), its projects and tasks, its login."""
    person = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        # The form works on its own copy: if it has a mistake, the rest of the page isn't affected
        form = AccountForm(request.POST, request.FILES, instance=User.objects.get(pk=pk), acting_user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Changes saved."))
            return redirect("admin_user", pk)
    else:
        form = AccountForm(instance=person, acting_user=request.user)

    # Every project the person manages or is a member of, with their open tasks in each one
    open_tasks = list(
        Task.objects.filter(assignee=person).exclude(status=Task.Status.DONE)
        .select_related("project").order_by(F("due_date").asc(nulls_last=True), "id")
    )
    open_per_project = Counter(task.project_id for task in open_tasks)
    managed = list(Project.objects.filter(manager=person).order_by("name"))
    member = list(Project.objects.filter(memberships__user=person).exclude(manager=person).order_by("name"))
    for project in managed + member:
        project.open_count = open_per_project.get(project.pk, 0)

    add_form = AddToProjectForm(person=person) if person.is_active else None
    context = {
        "section": "users",
        "person": person,
        "form": form,
        "managed_projects": managed,
        "member_projects": member,
        "project_count": len(managed) + len(member),
        "open_tasks": open_tasks,
        "add_form": add_form,
        "projects_to_add": add_form is not None and add_form.fields["project"].queryset.exists(),
        "failed_logins": failed_logins(request, username=person.username),
        "is_me": person.pk == request.user.pk,
    }
    return render(request, "administration/user_detail.html", context)


@admin_required
def user_password(request, pk):
    """Give someone a new password, for example when they forgot theirs."""
    person = get_object_or_404(User, pk=pk)
    if person.pk == request.user.pk:
        # Your own password is changed on your profile, where your current password is asked first
        return redirect("password_change")
    if request.method == "POST" and "generate" in request.POST:
        # "Make a random password": shown once, on a page that is not kept by the browser
        password = temporary_password()
        person.set_password(password)  # they are logged out wherever they were logged in
        person.mark_password_as_temporary()
        person.save()
        unlock(person.username)
        return passwords_sheet(request, [(person, password)])
    if request.method == "POST":
        form = NewPasswordForm(person, request.POST)
        if form.is_valid():
            form.save()  # they are logged out wherever they were logged in
            unlock(person.username)  # and a login locked after wrong passwords is unlocked
            messages.success(
                request,
                _("The new password of %(person)s is saved. Give it to them in person or by phone, "
                  "not by email. They must choose their own password at their next login.")
                % {"person": person},
            )
            return redirect("admin_user", pk)
    else:
        form = NewPasswordForm(person)
    return render(request, "administration/user_password.html", {"section": "users", "person": person, "form": form})


def passwords_sheet(request, accounts):
    """The one-time page with the temporary passwords. It is rendered, never redirected to: the passwords
    exist only in this response (they are not stored in a readable form), and the browser must not keep it."""
    response = render(request, "administration/passwords_sheet.html",
                      {"section": "users", "accounts": accounts, "days": settings.TEMPORARY_PASSWORD_DAYS})
    response["Cache-Control"] = "no-store"
    return response


@admin_required
def user_import(request):
    """Create many accounts at once from a list: first name, last name, work email address."""
    result = None
    if request.method == "POST":
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            result = read_people(form.cleaned_data["people"])
            if not result.errors:
                accounts, chosen = [], set()
                with transaction.atomic():  # all the accounts, or none
                    for _line, first, last, email in result.rows:
                        username = free_username(first, last, email, taken=chosen)
                        chosen.add(username)
                        person = User(username=username, first_name=first, last_name=last, email=email,
                                      role=User.Role.USER)
                        password = temporary_password()
                        person.set_password(password)
                        person.mark_password_as_temporary()
                        person.save()
                        accounts.append((person, password))
                return passwords_sheet(request, accounts)
    else:
        form = ImportForm()
    return render(request, "administration/user_import.html", {"section": "users", "form": form, "result": result})


@admin_required
def user_deactivate(request, pk):
    """Shows what deactivating the account means first; the account is deactivated when that page is sent.
    People are deactivated, never deleted: their projects, tasks, comments and history stay."""
    person = get_object_or_404(User, pk=pk)
    if person.pk == request.user.pk:
        messages.error(request, _("You can't deactivate your own account. Another admin can."))
        return redirect("admin_user", pk)
    if not person.is_active:
        messages.info(request, _("This account is deactivated already."))
        return redirect("admin_user", pk)

    if request.method == "POST":
        if leaves_no_active_admin(person, person.role, is_active=False):
            messages.error(request, NO_ADMIN_LEFT)
            return redirect("admin_user", pk)
        person.is_active = False
        person.save(update_fields=["is_active"])  # Django logs them out at their next click
        messages.success(
            request,
            _("The account of %(person)s is deactivated: they can't log in anymore. "
              "Their projects, tasks and comments are kept.") % {"person": person},
        )
        return redirect("admin_user", pk)

    context = {
        "section": "users",
        "person": person,
        "open_tasks": Task.objects.filter(assignee=person).exclude(status=Task.Status.DONE)
        .select_related("project").order_by("project__name", "id"),
        "managed_projects": Project.objects.filter(manager=person, status__in=Project.ACTIVE_STATUSES).order_by("name"),
    }
    return render(request, "administration/user_deactivate.html", context)


@admin_required
@require_POST
def user_reactivate(request, pk):
    person = get_object_or_404(User, pk=pk)
    if not person.is_active:
        person.is_active = True
        person.save(update_fields=["is_active"])
        messages.success(request, _("The account of %(person)s is active again: they can log in.") % {"person": person})
    return redirect("admin_user", pk)


# ---------- A person's projects ----------
# The same rules as the Team box of a project's page: only active people are added,
# the manager always stays in the team, and someone with open tasks in a project stays in it.


@admin_required
@require_POST
def user_add_project(request, pk):
    person = get_object_or_404(User, pk=pk)
    form = AddToProjectForm(request.POST, person=person)
    if form.is_valid():
        project = form.cleaned_data["project"]
        if project.add_to_team(person, added_by=request.user):  # recorded, so the Activity page shows it
            tell_project_event(request, Notification.Kind.ADDED_TO_TEAM, project, person)
        messages.success(
            request, _("%(person)s is now in the team of %(project)s.") % {"person": person, "project": project}
        )
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return redirect("admin_user", pk)


@admin_required
@require_POST
def user_remove_project(request, pk, project_pk):
    person = get_object_or_404(User, pk=pk)
    project = get_object_or_404(Project, pk=project_pk)
    names = {"person": person, "project": project}
    if person.pk == project.manager_id:
        messages.error(
            request,
            _("%(person)s manages %(project)s, and the manager is always in the team. "
              "To take them out, first choose another manager on the project's Edit page.") % names,
        )
        return redirect("admin_user", pk)

    membership = get_object_or_404(ProjectMember, project=project, user=person)
    open_count = project.open_tasks_of(person).count()
    if open_count:
        messages.error(
            request,
            ngettext(
                "%(person)s still has %(count)d open task in %(project)s. "
                "Give it to someone else (or finish it) first.",
                "%(person)s still has %(count)d open tasks in %(project)s. "
                "Give them to someone else (or finish them) first.",
                open_count,
            ) % {**names, "count": open_count},
        )
    else:
        membership.delete()  # recorded, so the Activity page shows it
        messages.success(request, _("%(person)s is no longer in the team of %(project)s.") % names)
    return redirect("admin_user", pk)


# ---------- Projects and tasks ----------
# Admins edit any project, task or team from its own page; these two lists show everything at once.


@admin_required
def project_list(request):
    """Every project, with its manager and its team. Each one can be opened, edited or deleted."""
    projects = (
        Project.objects.with_task_counts().select_related("manager").prefetch_related("members").order_by("name")
    )
    status = request.GET.get("status", "all")  # all (the default), active, or one status
    if status == "active":
        projects = projects.filter(status__in=Project.ACTIVE_STATUSES)
    elif status in Project.Status.values:
        projects = projects.filter(status=status)
    else:
        status = "all"
    query = request.GET.get("q", "").strip()
    if query:
        projects = projects.filter(name__icontains=query)

    projects = list(projects)
    for project in projects:
        project.open_count = project.task_total - project.task_done
        # The members, besides the manager (the manager is shown on their own)
        project.other_members = sorted(
            (person for person in project.members.all() if person.pk != project.manager_id),
            key=lambda person: str(person).lower(),
        )
    context = {
        "section": "projects",
        "projects": projects,
        "status": status,
        "query": query,
        "status_choices": Project.Status.choices,
    }
    return render(request, "administration/project_list.html", context)


TASKS_PER_PAGE = 50


@admin_required
def task_list(request):
    """Every task of every project, with filters: open, late or done; the project; the person;
    words of the title. Long lists are cut into pages of 50."""
    tasks = Task.objects.select_related("project", "assignee", "parent")

    state = request.GET.get("state", "open")  # open (the default), late, done or all
    if state == "late":
        tasks = tasks.filter(due_date__lt=timezone.localdate()).exclude(status=Task.Status.DONE)
    elif state == "done":
        tasks = tasks.filter(status=Task.Status.DONE)
    elif state != "all":
        state = "open"
        tasks = tasks.exclude(status=Task.Status.DONE)

    project = request.GET.get("project", "")
    if project.isdigit():
        tasks = tasks.filter(project_id=project)
    else:
        project = ""

    person = request.GET.get("person", "")  # someone's number, or "none" for the tasks given to nobody
    if person == "none":
        tasks = tasks.filter(assignee__isnull=True)
    elif person.isdigit():
        tasks = tasks.filter(assignee_id=person)
    else:
        person = ""

    query = request.GET.get("q", "").strip()
    if query:
        tasks = tasks.filter(title__icontains=query)

    if state == "done":
        tasks = tasks.order_by(F("completed_at").desc(nulls_last=True), "-id")  # the latest finished first
    else:
        tasks = tasks.order_by(F("due_date").asc(nulls_last=True), "id")  # the most urgent first

    page = Paginator(tasks, TASKS_PER_PAGE).get_page(request.GET.get("page"))
    # The filters, to keep them in the links to the other pages of the list
    filters = request.GET.copy()
    filters.pop("page", None)
    context = {
        "section": "tasks",
        "page": page,
        "state": state,
        "project": project,
        "person": person,
        "query": query,
        "filters": filters.urlencode(),
        "projects": Project.objects.order_by("name"),
        # Everyone who has tasks, even with a deactivated account
        "people": User.objects.filter(pk__in=Task.objects.values("assignee")).order_by(
            "first_name", "last_name", "username"
        ),
        "filtered": bool(project or person or query or state != "open"),
    }
    return render(request, "administration/task_list.html", context)


# ---------- Locked logins ----------


@admin_required
def login_list(request):
    """The wrong passwords that still count, and the logins they locked."""
    cool_off_minutes = int(get_cool_off(request).total_seconds() // 60)
    context = {
        "section": "logins",
        "groups": failed_logins(request),
        "failure_limit": get_failure_limit(request, None),
        "cool_off_hours": cool_off_minutes // 60,
        "cool_off_minutes": cool_off_minutes,
    }
    return render(request, "administration/login_list.html", context)


@admin_required
@require_POST
def login_unlock(request):
    form = UnlockForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("This login couldn't be found."))
    elif unlock(form.cleaned_data["username"], form.cleaned_data["ip_address"]):
        messages.success(request, _("The login with the username %(username)s is unlocked.") % form.cleaned_data)
    else:
        messages.info(request, _("There was nothing to unlock: these wrong passwords don't count anymore."))

    # The button can ask to come back to its own page (like a person's page) with a "next" field.
    # Only addresses on this site are accepted, so a link can't send people elsewhere.
    next_page = request.POST.get("next")
    if next_page and url_has_allowed_host_and_scheme(
        next_page, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_page)
    return redirect("admin_logins")


# ---------- Feedback (Step 25) ----------

FEEDBACK_PER_PAGE = 20


@admin_required
def feedback_list(request):
    """What people think of the app: the average rating, how the ratings are shared, and the messages."""
    everything = Feedback.objects.all()
    total = everything.count()
    by_rating = {row["rating"]: row["n"] for row in everything.values("rating").annotate(n=Count("id"))}
    notes = everything.select_related("author", "reviewed_by")

    rating = request.GET.get("rating", "")
    kind = request.GET.get("kind", "")
    state = request.GET.get("state", "")
    if rating in {str(number) for number, _label in Feedback.RATINGS}:
        notes = notes.filter(rating=int(rating))
    else:
        rating = ""
    if kind in Feedback.Kind.values:
        notes = notes.filter(kind=kind)
    else:
        kind = ""
    if state == "new":
        notes = notes.filter(reviewed_at__isnull=True)
    elif state == "reviewed":
        notes = notes.filter(reviewed_at__isnull=False)
    else:
        state = ""

    context = {
        "section": "feedback",
        "total": total,
        "average": everything.aggregate(average=Avg("rating"))["average"],
        "new_count": everything.filter(reviewed_at__isnull=True).count(),
        "distribution": [
            {"stars": number, "label": label, "count": by_rating.get(number, 0),
             "percent": round(100 * by_rating.get(number, 0) / total) if total else 0}
            for number, label in reversed(Feedback.RATINGS)
        ],
        "page": Paginator(notes, FEEDBACK_PER_PAGE).get_page(request.GET.get("page")),
        "ratings": Feedback.RATINGS,
        "kinds": Feedback.Kind.choices,
        "rating": rating,
        "kind": kind,
        "state": state,
    }
    return render(request, "administration/feedback_list.html", context)


@admin_required
@require_POST
def feedback_review(request, pk):
    """Mark a remark as read (or put it back as new)."""
    note = get_object_or_404(Feedback, pk=pk)
    if note.reviewed_at is None:
        note.reviewed_at, note.reviewed_by = timezone.now(), request.user
    else:
        note.reviewed_at, note.reviewed_by = None, None
    note.save(update_fields=["reviewed_at", "reviewed_by"])
    next_page = request.POST.get("next")
    if next_page and url_has_allowed_host_and_scheme(
        next_page, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_page)
    return redirect("admin_feedback")
