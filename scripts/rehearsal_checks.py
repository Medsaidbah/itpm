"""Checks for the migration rehearsal (scripts/rehearse-migrations.ps1), run inside the rehearsal container:

    python scripts/rehearsal_checks.py counts   ->  one line per table: "<table> <number of rows>"
    python scripts/rehearsal_checks.py pages    ->  one line per page:  "<who> <page> <status code> [<redirect path>]"

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


if __name__ == "__main__":
    {"counts": counts, "pages": pages}[sys.argv[1]]()
