"""The roles in the whole app become Admin and User (Stage 16c).

Being a manager or a member is now decided project by project: a project's manager is
chosen on the project, and its members are in its team. So the old "Manager" and "Member"
roles both become "User". Admins stay admins. Nobody's projects or tasks change.

Going back (unapplying) turns the Users who manage a project into Managers again,
and the other Users into Members.
"""

from django.db import migrations, models


def to_admin_and_user(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.exclude(role="admin").update(role="user")


def back_to_manager_and_member(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Project = apps.get_model("projects", "Project")
    User.objects.filter(role="user", pk__in=Project.objects.values("manager")).update(role="manager")
    User.objects.filter(role="user").update(role="member")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("projects", "0003_fill_teams"),  # going back reads who manages a project
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[("admin", "Admin"), ("user", "User")],
                default="user",
                max_length=20,
            ),
        ),
        migrations.RunPython(to_admin_and_user, back_to_manager_and_member),
    ]
