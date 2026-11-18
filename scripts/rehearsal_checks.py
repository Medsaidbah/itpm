"""Checks for the migration rehearsal (scripts/rehearse-migrations.ps1), run inside the rehearsal container:

    python scripts/rehearsal_checks.py counts   ->  one line per table: "<table> <number of rows>"
    python scripts/rehearsal_checks.py pages    ->  one line per page:  "<who> <page> <status code>"

It prints only counts, page labels and status codes: never a name, an email or anything else read from the rows
(the database is a copy of the live one). The pages are opened with Django's test client, logged in as an admin
and as a user of the copy; this adds sessions to the rehearsal database only.
"""

import os
import sys
from pathlib import Path

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


def pages():
    from accounts.models import User
    from projects.models import Project, ProjectMember, Task

    admin = User.objects.filter(role=User.Role.ADMIN, is_active=True).order_by("pk").first()
    in_a_team = ProjectMember.objects.values("user")
    user = (User.objects.filter(role=User.Role.USER, is_active=True)
            .filter(Q(pk__in=in_a_team) | Q(managed_projects__isnull=False)).order_by("pk").first())
    for who, person in (("admin", admin), ("user", user)):
        if person is None:
            print(who, "none-in-the-copy", "-")
            continue
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
                          ("admin-people", "/administration/people/")]
        for label, address in addresses:
            print(who, label, client.get(address).status_code)


if __name__ == "__main__":
    {"counts": counts, "pages": pages}[sys.argv[1]]()
