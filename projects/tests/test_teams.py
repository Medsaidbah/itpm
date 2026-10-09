"""Project teams (Step 16a): who is in each team, and how the existing data filled the teams."""

import importlib

from django.apps import apps
from django.db import IntegrityError, transaction

from accounts.models import User
from projects.models import Attachment, Comment, Project, ProjectMember, Task

from .base import TeamTestCase

# The migration that filled the teams. Its name starts with a number, so it's loaded this way.
fill_teams_migration = importlib.import_module("projects.migrations.0003_fill_teams")


class TeamTests(TeamTestCase):
    def test_the_manager_comes_first_then_the_members_by_name(self):
        ProjectMember.objects.create(project=self.project, user=self.sara)
        ProjectMember.objects.create(project=self.project, user=self.karim)
        self.assertEqual(self.project.team(), [self.omar, self.karim, self.sara])

    def test_the_manager_is_never_listed_twice(self):
        ProjectMember.objects.create(project=self.project, user=self.omar)  # the manager, added by mistake
        self.assertEqual(self.project.team(), [self.omar])

    def test_a_person_is_only_once_in_a_team(self):
        ProjectMember.objects.create(project=self.project, user=self.sara)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectMember.objects.create(project=self.project, user=self.sara)

    def test_a_person_can_be_in_several_teams(self):
        other = Project.objects.create(name="Wi-Fi", manager=self.sara)  # Sara manages this one
        ProjectMember.objects.create(project=self.project, user=self.sara)  # and is a member here
        self.assertEqual(list(self.sara.member_projects.all()), [self.project])
        self.assertEqual(list(self.sara.managed_projects.all()), [other])

    def test_project_page_shows_the_team(self):
        ProjectMember.objects.create(project=self.project, user=self.sara)
        self.login(self.karim)
        response = self.client.get(f"/projects/{self.project.pk}/")
        self.assertEqual(response.context["team"], [self.omar, self.sara])
        self.assertContains(response, "Team")


class FillTeamsMigrationTests(TeamTestCase):
    """The migration of Step 16a puts in each team the people already involved in the project."""

    def test_people_involved_join_the_team(self):
        commenter = User.objects.create_user("lina", "lina@example.com")
        uploader = User.objects.create_user("ali", "ali@example.com")
        editor = User.objects.create_user("moussa", "moussa@example.com", role="manager")
        outsider = User.objects.create_user("nobody", "nobody@example.com")
        admin_who_commented = User.objects.create_user("boss", "boss@example.com", role="admin")

        # Sara has a task (from base.py). Amina, an admin, is assigned a task too.
        Task.objects.create(project=self.project, title="Admin's own task", assignee=self.amina)
        Comment.objects.create(task=self.task, author=commenter, body="Hello")
        Comment.objects.create(task=self.task, author=admin_who_commented, body="Hi")
        Attachment.objects.create(task=self.task, uploaded_by=uploader, file="attachments/x.pdf")
        self.task._history_user = editor  # the change history remembers who edited the task
        self.task.title = "Install the new firewall"
        self.task.save()

        fill_teams_migration.fill_teams(apps, None)

        members = set(self.project.members.all())
        self.assertEqual(members, {self.sara, self.amina, commenter, uploader, editor})
        self.assertNotIn(outsider, members)  # never involved
        self.assertNotIn(admin_who_commented, members)  # admins can see every project anyway
        self.assertNotIn(self.omar, members)  # the manager is always in the team already

        fill_teams_migration.fill_teams(apps, None)  # running it again adds nobody twice
        self.assertEqual(self.project.memberships.count(), 5)


class AdminTeamTests(TeamTestCase):
    def test_the_admin_panel_edits_the_team_and_remembers_who_added(self):
        self.login(self.amina)
        url = f"/admin/projects/project/{self.project.pk}/change/"
        self.assertContains(self.client.get(url), "Team (besides the manager)")
        data = {
            "name": "Firewall", "description": "", "status": "in_progress", "priority": "medium",
            "manager": self.omar.pk, "start_date": "", "end_date": "",
            # The team: one new line, for Karim
            "memberships-TOTAL_FORMS": "1", "memberships-INITIAL_FORMS": "0",
            "memberships-MIN_NUM_FORMS": "0", "memberships-MAX_NUM_FORMS": "1000",
            "memberships-0-user": self.karim.pk, "memberships-0-project": self.project.pk,
            # The tasks below the project: the existing one, unchanged
            "tasks-TOTAL_FORMS": "1", "tasks-INITIAL_FORMS": "1",
            "tasks-MIN_NUM_FORMS": "0", "tasks-MAX_NUM_FORMS": "1000",
            "tasks-0-id": self.task.pk, "tasks-0-project": self.project.pk,
            "tasks-0-title": self.task.title, "tasks-0-status": "todo",
            "tasks-0-assignee": self.sara.pk, "tasks-0-start_date": "",
            "tasks-0-due_date": self.task.due_date.isoformat(),
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content.decode()[:3000])
        membership = ProjectMember.objects.get(project=self.project, user=self.karim)
        self.assertEqual(membership.added_by, self.amina)
        self.assertEqual(membership.history.first().history_type, "+")  # the change history saw it
