"""Fills each project's team with the people already involved in it (Step 16a).

Before Step 16a there were no teams: everyone could open every project. So that nobody
loses the projects they work on, each team gets the people who:
  - have a task assigned in the project,
  - wrote a comment or added a file on one of its tasks,
  - edited the project or one of its tasks (from the change history).
Admins are only added when a task is assigned to them: they can open every project anyway.
The project's manager isn't added either: the manager is always part of the team.

Nothing is deleted. Running it a second time adds nobody twice.
"""

from django.db import migrations


def fill_teams(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    ProjectMember = apps.get_model("projects", "ProjectMember")
    Task = apps.get_model("projects", "Task")
    Comment = apps.get_model("projects", "Comment")
    Attachment = apps.get_model("projects", "Attachment")
    HistoricalProject = apps.get_model("projects", "HistoricalProject")
    HistoricalTask = apps.get_model("projects", "HistoricalTask")
    User = apps.get_model("accounts", "User")

    admins = set(User.objects.filter(role="admin").values_list("pk", flat=True))
    new_members = []
    for project in Project.objects.all():
        assigned = set(
            Task.objects.filter(project=project, assignee__isnull=False).values_list("assignee", flat=True)
        )
        involved = set(Comment.objects.filter(task__project=project).values_list("author", flat=True))
        involved |= set(Attachment.objects.filter(task__project=project).values_list("uploaded_by", flat=True))
        involved |= set(
            HistoricalTask.objects.filter(project_id=project.pk, history_user__isnull=False)
            .values_list("history_user", flat=True)
        )
        involved |= set(
            HistoricalProject.objects.filter(id=project.pk, history_user__isnull=False)
            .values_list("history_user", flat=True)
        )
        team = assigned | (involved - admins)
        team.discard(project.manager_id)
        new_members += [ProjectMember(project=project, user_id=person) for person in sorted(team)]

    # ignore_conflicts: someone already in a team is simply skipped
    ProjectMember.objects.bulk_create(new_members, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0002_project_teams"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        # Going back (unapplying) has nothing to undo here: 0002 then removes the whole table
        migrations.RunPython(fill_teams, migrations.RunPython.noop),
    ]
