"""Rights decided project by project (Stage 16c).

1. Any active person can now be chosen as a project's manager (before: only admins and managers).
   This changes no table: it only changes the list of names in the forms.
2. From now on, people only see the projects whose team they are in. So that nobody loses
   a task, everyone who has an open task in a project is put in its team, if they aren't yet.
   Running it again adds nobody twice.
3. The app only links tasks of the same project, but the admin panel could link two projects.
   Such a link would show a task's title to people outside its project, so:
   - a "waits for" link between tasks of two different projects is removed;
   - a subtask whose main task is in another project becomes a main task.
   Usually there are none. No task, comment or file is deleted.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F


def put_open_task_people_in_teams(apps, schema_editor):
    Task = apps.get_model("projects", "Task")
    ProjectMember = apps.get_model("projects", "ProjectMember")

    open_tasks = Task.objects.exclude(status="done").filter(assignee__isnull=False)
    pairs = set(open_tasks.values_list("project_id", "assignee_id", "project__manager_id"))
    new_members = [
        ProjectMember(project_id=project, user_id=person)
        for project, person, manager in sorted(pairs)
        if person != manager  # the manager is always part of the team
    ]
    # ignore_conflicts: someone already in the team is simply skipped
    ProjectMember.objects.bulk_create(new_members, ignore_conflicts=True)


def remove_links_across_projects(apps, schema_editor):
    Task = apps.get_model("projects", "Task")
    WaitsFor = Task.depends_on.through  # one line per "this task waits for that task"
    WaitsFor.objects.exclude(from_task__project=F("to_task__project")).delete()
    Task.objects.filter(parent__isnull=False).exclude(parent__project=F("project")).update(parent=None)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0003_fill_teams"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="historicalproject",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                limit_choices_to={"is_active": True},
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="project",
            name="manager",
            field=models.ForeignKey(
                limit_choices_to={"is_active": True},
                on_delete=django.db.models.deletion.PROTECT,
                related_name="managed_projects",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Going back (unapplying) has nothing to undo: the people stay in the teams,
        # and the links between two projects aren't put back
        migrations.RunPython(put_open_task_people_in_teams, migrations.RunPython.noop),
        migrations.RunPython(remove_links_across_projects, migrations.RunPython.noop),
    ]
