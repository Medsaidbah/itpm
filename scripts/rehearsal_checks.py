"""Checks for the migration rehearsal (scripts/rehearse-migrations.ps1), run inside the rehearsal container:

    python scripts/rehearsal_checks.py counts   ->  one line per table: "<table> <number of rows>"
    python scripts/rehearsal_checks.py pages    ->  one line per page:  "<who> <page> <status code> [<redirect path>]"
    python scripts/rehearsal_checks.py fingerprint            ->  "<table> <hash> <columns>" for every table
    python scripts/rehearsal_checks.py fingerprint-of T=a,b   ->  "<table> <hash>", over those columns only
    python scripts/rehearsal_checks.py defaults               ->  "projects-defaults ok" (release 1.3 and later)
    python scripts/rehearsal_checks.py progress               ->  "<project>:<progress>:<expected>" (kept, not shown)
    python scripts/rehearsal_checks.py progress-compare ...   ->  "progress-same ok" or "progress-same <n>-projects-differ"

A fingerprint is a hash of every row of a table, over the given columns: the same hash before and after the
migrations proves that no old value changed, without printing any value.

It prints only counts, page labels, status codes and, for a redirect, the path it goes to (never its query string):
never a name, an email or anything else read from the rows (the database is a copy of the live one).
The pages are opened with Django's test client, logged in as an admin and as a user of the copy who chose their
own password: someone who still has a temporary password is sent to "change my password" on every page. If the copy
has no such user, the flag is cleared for one of them, in the rehearsal database only, and a "note" line says so.
Every page of the list must answer 200; the rehearsal script counts anything else as a failure.
From release 1.2, an executive made for the check (and deleted afterwards) opens the read pages (200 each), tries
every kind of change ("write-..." lines: each must be refused, 403) and checks that nothing changed.
This adds sessions to the rehearsal database only.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.db.models import Q  # noqa: E402
from django.test import Client  # noqa: E402


def counts():
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.table_names()):
            cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}")
            print(table, cursor.fetchone()[0])


BOOKKEEPING = {"django_migrations", "django_content_type", "auth_permission"}  # grow with new migrations


def table_hash(cursor, table, columns):
    quote = connection.ops.quote_name
    cursor.execute(f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
                   f"FROM (SELECT {', '.join(quote(c) for c in columns)} FROM {quote(table)}) t")
    return cursor.fetchone()[0]


def fingerprint():
    with connection.cursor() as cursor:
        for table in sorted(set(connection.introspection.table_names()) - BOOKKEEPING):
            columns = [c.name for c in connection.introspection.get_table_description(cursor, table)]
            print(table, table_hash(cursor, table, columns), ",".join(columns))


def fingerprint_of(specs):
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names())
        for spec in specs:
            table, columns = spec.split("=", 1)
            if table not in tables:
                print(table, "gone")
                continue
            present = {c.name for c in connection.introspection.get_table_description(cursor, table)}
            columns = columns.split(",")
            if not set(columns) <= present:
                print(table, "columns-gone")
                continue
            print(table, table_hash(cursor, table, columns))


def defaults():
    """Release 1.3: right after the migration, every project and history row is "not specified", without partner or
    budget. Prints nothing before release 1.3."""
    with connection.cursor() as cursor:
        if "kind" not in {c.name for c in connection.introspection.get_table_description(cursor, "projects_project")}:
            return
        wrong = 0
        for table in ("projects_project", "projects_historicalproject"):
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE kind <> 'unspecified' OR partner <> '' "
                           f"OR budget IS NOT NULL")
            wrong += cursor.fetchone()[0]
    print("projects-defaults", "ok" if wrong == 0 else f"{wrong}-rows-differ")


def describe(response):
    """"200", or for a redirect "302 /path/" (the path only: a query string could hold anything)."""
    if response.status_code in (301, 302, 303, 307, 308):
        return f"{response.status_code} {urlsplit(response['Location']).path or '/'}"
    return str(response.status_code)


def pick(people):
    """Someone of this group who chose their own password; else one of them, with the flag cleared here only
    (this database is the rehearsal's copy). Returns (person, cleared)."""
    person = people.filter(must_change_password=False).order_by("pk").first()
    if person:
        return person, False
    person = people.order_by("pk").first()
    if person:
        type(person).objects.filter(pk=person.pk).update(must_change_password=False, temporary_password_until=None)
        person.refresh_from_db()
    return person, person is not None


def pages():
    from accounts.models import User
    from projects.models import Project, ProjectMember, Task

    active = User.objects.filter(is_active=True)
    in_a_team = ProjectMember.objects.values("user")
    admin, admin_cleared = pick(active.filter(role=User.Role.ADMIN))
    user, user_cleared = pick(active.filter(role=User.Role.USER)
                              .filter(Q(pk__in=in_a_team) | Q(managed_projects__isnull=False)).distinct())
    for who, person, cleared in (("admin", admin, admin_cleared), ("user", user, user_cleared)):
        if person is None:
            print(who, "none-in-the-copy", "-")
            continue
        if cleared:
            print(who, "note", "password-flag-cleared-in-the-copy")
        client = Client(raise_request_exception=False)
        client.force_login(person)
        project = Project.objects.visible_to(person).order_by("pk").first()
        task = Task.objects.visible_to(person).order_by("pk").first()
        addresses = [("home", "/"), ("my-tasks", "/my-tasks/"), ("projects", "/projects/?status=all"),
                     ("dashboard", "/dashboard/"), ("activity", "/activity/"), ("notifications", "/notifications/"),
                     ("own-page", f"/people/{person.pk}/"), ("profile", "/profile/")]
        if project:
            addresses += [("project", f"/projects/{project.pk}/"), ("board", f"/projects/{project.pk}/board/"),
                          ("timeline", f"/projects/{project.pk}/timeline/"),
                          ("report", f"/projects/{project.pk}/report/"),
                          ("project-excel", f"/projects/{project.pk}/export.xlsx")]
        if task:
            addresses.append(("task", f"/tasks/{task.pk}/"))
        if who == "admin":
            addresses += [("administration", "/administration/"), ("admin-users", "/administration/users/"),
                          ("admin-projects", "/administration/projects/"), ("admin-tasks", "/administration/tasks/"),
                          ("people-overview", "/people/overview/")]
        for label, address in addresses:
            print(who, label, describe(client.get(address)))
    if "executive" in User.Role.values:  # release 1.2 and later
        executive_pages()
    if any(f.name == "budget" for f in Project._meta.get_fields()):  # release 1.3 and later
        budget_pages(admin)


def snapshot():
    """What an executive must never change, as numbers only: rows per table that matter, and the latest changes."""
    from django.db.models import Max

    from accounts.models import User
    from projects.models import Attachment, Comment, Project, ProjectMember, Task

    return (Project.objects.count(), Task.objects.count(), Comment.objects.count(), Attachment.objects.count(),
            ProjectMember.objects.count(), User.objects.count(),
            Project.objects.aggregate(last=Max("updated_at"))["last"], Task.objects.aggregate(last=Max("updated_at"))["last"],
            sorted(Task.objects.values_list("pk", "status")), sorted(ProjectMember.objects.values_list("pk", "is_manager")))


def executive_pages():
    """Release 1.2: an executive, made here for the check only (in the rehearsal database) and deleted afterwards.
    Every read page must answer 200; every change must be refused (403) and change nothing.
    Lines: "executive <page> <status>", "executive write-<change> <status>", "executive nothing-changed ok|changed"."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from accounts.models import User
    from projects.models import Attachment, Comment, Project, Task

    User.objects.filter(username="rehearsal-executive").delete()
    executive = User.objects.create_user("rehearsal-executive", role=User.Role.EXECUTIVE)
    client = Client(raise_request_exception=False)
    try:
        client.force_login(executive)
        project = Project.objects.order_by("pk").first()
        task = Task.objects.order_by("pk").first()
        other = User.objects.filter(is_active=True).exclude(pk=executive.pk).order_by("pk").first()
        reads = [("home", "/"), ("my-tasks", "/my-tasks/"), ("projects", "/projects/?status=all"),
                 ("dashboard", "/dashboard/"), ("activity", "/activity/"), ("notifications", "/notifications/"),
                 ("own-page", f"/people/{executive.pk}/"), ("profile", "/profile/"),
                 ("people-overview", "/people/overview/"), ("projects-excel", "/projects/export.xlsx")]
        if other:
            reads.append(("person-page", f"/people/{other.pk}/"))
        if project:
            reads += [("project", f"/projects/{project.pk}/"), ("board", f"/projects/{project.pk}/board/"),
                      ("timeline", f"/projects/{project.pk}/timeline/"), ("report", f"/projects/{project.pk}/report/"),
                      ("project-excel", f"/projects/{project.pk}/export.xlsx")]
        if task:
            reads.append(("task", f"/tasks/{task.pk}/"))
        for label, address in reads:
            print("executive", label, describe(client.get(address)))

        before = snapshot()
        writes = [("write-project-create", "/projects/new/", {"name": "x"}),
                  ("write-admin-user-create", "/administration/users/new/", {"username": "x"})]
        if project:
            p = project.pk
            writes += [("write-project-edit", f"/projects/{p}/edit/", {"name": "x"}),
                       ("write-project-delete", f"/projects/{p}/delete/", {"name": project.name}),
                       ("write-team-add", f"/projects/{p}/team/add/", {"person": executive.pk}),
                       ("write-task-create", f"/projects/{p}/tasks/new/", {"title": "x", "status": "todo"})]
            if other:
                writes += [("write-team-remove", f"/projects/{p}/team/{other.pk}/remove/", {}),
                           ("write-team-manager", f"/projects/{p}/team/{other.pk}/manager/", {"manager": "1"})]
        if task:
            t = task.pk
            writes += [("write-task-status", f"/tasks/{t}/status/", {"status": "blocked", "blocked_reason": "x"}),
                       ("write-task-move", f"/tasks/{t}/move/", {"status": "in_progress", "order": [t]}),
                       ("write-task-dates", f"/tasks/{t}/dates/", {"start_date": "2030-01-01", "due_date": "2030-01-02"}),
                       ("write-task-edit", f"/tasks/{t}/edit/", {"title": "x", "status": "todo"}),
                       ("write-task-delete", f"/tasks/{t}/delete/", {}),
                       ("write-comment-add", f"/tasks/{t}/comments/add/", {"body": "x"}),
                       ("write-file-add", f"/tasks/{t}/files/add/", {"file": SimpleUploadedFile("x.txt", b"x")})]
        comment = Comment.objects.order_by("pk").first()
        if comment:
            writes.append(("write-comment-delete", f"/comments/{comment.pk}/delete/", {}))
        attachment = Attachment.objects.order_by("pk").first()
        if attachment:
            writes.append(("write-file-delete", f"/files/{attachment.pk}/delete/", {}))
        for label, address, data in writes:
            print("executive", label, describe(client.post(address, data)))
        print("executive", "nothing-changed", "ok" if snapshot() == before else "changed")
    finally:
        client.logout()  # their session goes
        executive.delete()  # and so does the account: the copy is left as it was





BUDGET = 987654321  # a number that appears nowhere else


def marker_forms():
    """Every way the marker could be written: plain digits, and as each language of the app formats it."""
    from django.utils import translation

    from projects.money import format_mru

    forms = set()
    for amount in (BUDGET,):
        forms |= {str(amount), f"{amount:,}", f"{amount:,}".replace(",", " "), f"{amount:,}".replace(",", "\u00a0"),
                  f"{amount:,}".replace(",", ".")}
        for language in ("en", "fr", "ar"):
            with translation.override(language):
                forms.add(format_mru(amount).split("\u00a0MRU")[0])
    return forms


def as_text(response):
    """A page, or every sheet of an Excel file (unzipped), as text."""
    import io
    import zipfile

    body = b"".join(response.streaming_content) if response.streaming else response.content
    if body[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            return "".join(archive.read(name).decode("utf-8", "replace") for name in archive.namelist())
    return body.decode("utf-8", "replace")


def budget_pages(admin):
    """Release 1.3: a marker budget is put on one project, in the rehearsal database only. An ordinary member
    of its team must not find it on any page, export or Activity; its manager, an admin and an executive (made for
    the check) must see it on the project's page. Everything is put back afterwards.
    Lines: "member budget-hidden-<page> ok|LEAK" and "<who> budget-shown ok|missing"."""
    from django.db.models import F

    from accounts.models import User
    from projects.models import Project, ProjectMember

    member_line = (ProjectMember.objects.filter(is_manager=False, user__is_active=True, user__role=User.Role.USER)
                   .exclude(user=F("project__manager")).order_by("user__must_change_password", "pk").first())
    if member_line is None:
        print("member", "budget-none-in-the-copy", "-")
        return
    project = Project.objects.get(pk=member_line.project_id)
    member, manager = member_line.user, project.manager
    flags = {person.pk: (person.must_change_password, person.temporary_password_until) for person in (member, manager)}
    for who, person in (("member", member), ("manager", manager)):
        if person.must_change_password:  # in the rehearsal copy only, like for the pages above
            User.objects.filter(pk=person.pk).update(must_change_password=False, temporary_password_until=None)
            print(who, "note", "password-flag-cleared-in-the-copy")
    saved = Project.objects.filter(pk=project.pk).values("budget", "status", "updated_at").get()
    last_version = Project.history.order_by("-history_id").values_list("history_id", flat=True).first() or 0
    executive = User.objects.create_user("rehearsal-executive-budget", role=User.Role.EXECUTIVE)
    clients = []
    try:
        project.budget = BUDGET
        project.save()  # a change in the history too, for the Activity
        forms = marker_forms()

        client = Client(raise_request_exception=False)
        client.force_login(member)
        clients.append(client)
        p = project.pk
        for label, address in (("project", f"/projects/{p}/"), ("report", f"/projects/{p}/report/"),
                               ("project-excel", f"/projects/{p}/export.xlsx"), ("projects", "/projects/?status=all"),
                               ("projects-excel", "/projects/export.xlsx"), ("activity", f"/activity/?project={p}"),
                               ("home", "/"), ("dashboard", "/dashboard/")):
            text = as_text(client.get(address))
            print("member", f"budget-hidden-{label}", "LEAK" if any(form in text for form in forms) else "ok")

        for who, person in (("manager", manager), ("admin", admin), ("executive", executive)):
            if person is None:
                continue
            viewer = Client(raise_request_exception=False)
            viewer.force_login(person)
            clients.append(viewer)
            text = as_text(viewer.get(f"/projects/{p}/"))
            print(who, "budget-shown", "ok" if any(form in text for form in forms) else "missing")
    finally:
        for client in clients:
            client.logout()
        Project.objects.filter(pk=project.pk).update(**saved)  # the copy as it was
        Project.history.filter(history_id__gt=last_version).delete()
        for pk, (must_change, until) in flags.items():  # their temporary passwords too
            User.objects.filter(pk=pk).update(must_change_password=must_change, temporary_password_until=until)
        executive.delete()


def progress_figures():
    """{project id: (progress, expected progress or None)}, worked out from the tables themselves, the way the app
    does (Project.progress and projects/schedule.py): weighted when the tasks have a weight, every task counting 1
    before that column exists. It reads the tables directly, so it works before the migrations too (the models of
    the branch would ask for columns the copy doesn't have yet)."""
    from django.utils import timezone

    from projects.schedule import time_elapsed

    today = timezone.localdate()
    with connection.cursor() as cursor:
        task_columns = {c.name for c in connection.introspection.get_table_description(cursor, "projects_task")}
        weight = "weight" if "weight" in task_columns else "1"
        cursor.execute(f"SELECT project_id, status, due_date, {weight} FROM projects_task")
        tasks = cursor.fetchall()
        cursor.execute("SELECT id, status, start_date, end_date FROM projects_project")
        projects = cursor.fetchall()
    per_project = {}
    for project_id, status, due, task_weight in tasks:
        per_project.setdefault(project_id, []).append((status, due, task_weight))
    figures = {}
    for pk, status, start, end in projects:
        rows = per_project.get(pk, [])
        total = sum(row[2] for row in rows)
        done = sum(row[2] for row in rows if row[0] == "done")
        progress = round(done * 100 / total) if total else 0
        expected = None
        if status in ("planned", "in_progress") and rows:
            if any(row[1] for row in rows):  # at least one task has a due date
                due = sum(row[2] for row in rows if row[1] and row[1] < today)
                expected = round(due * 100 / total)
            elif start and end:
                expected = time_elapsed(start, end, today)
        figures[pk] = (progress, expected)
    return figures


def progress():
    """One line per project, kept by the rehearsal script to compare after migrating (not put in the report)."""
    for pk, (done, expected) in sorted(progress_figures().items()):
        print(f"{pk}:{done}:{expected}")


def progress_compare(before):
    """Release 1.4: every project's progress and expected progress must be the same after migrating."""
    old = {}
    for item in before:
        pk, done, expected = item.split(":")
        old[int(pk)] = (int(done), None if expected == "None" else int(expected))
    now = progress_figures()
    differ = sum(1 for pk in set(old) | set(now) if old.get(pk) != now.get(pk))
    print("progress-same", "ok" if differ == 0 else f"{differ}-projects-differ")


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "fingerprint-of":
        fingerprint_of(sys.argv[2:])
    elif command == "progress-compare":
        progress_compare(sys.argv[2:])
    else:
        {"counts": counts, "pages": pages, "fingerprint": fingerprint, "defaults": defaults,
         "progress": progress}[command]()
