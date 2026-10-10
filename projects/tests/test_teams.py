"""Project teams: who is in each team, how the existing data filled the teams (Stage 16a),
and how the team is managed and used (Stage 16b)."""

import importlib

from django.apps import apps
from django.db import IntegrityError, transaction

from accounts.models import User
from projects.models import Attachment, Comment, Project, ProjectMember, Task

from .base import TeamTestCase
from .test_activity import visible_text

# The migration that filled the teams. Its name starts with a number, so it's loaded this way.
fill_teams_migration = importlib.import_module("projects.migrations.0003_fill_teams")


class TeamTests(TeamTestCase):
    def test_the_manager_comes_first_then_the_members_by_name(self):
        ProjectMember.objects.create(project=self.project, user=self.karim)
        self.assertEqual(self.project.team(), [self.omar, self.karim, self.sara])

    def test_the_manager_is_never_listed_twice(self):
        ProjectMember.objects.create(project=self.project, user=self.omar)  # the manager, added by mistake
        self.assertEqual(self.project.team(), [self.omar, self.sara])

    def test_a_person_is_only_once_in_a_team(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectMember.objects.create(project=self.project, user=self.sara)  # Sara is in it already

    def test_a_person_can_be_in_several_teams(self):
        other = Project.objects.create(name="Wi-Fi", manager=self.sara)  # Sara manages this one
        self.assertEqual(list(self.sara.member_projects.all()), [self.project])  # and is a member here
        self.assertEqual(list(self.sara.managed_projects.all()), [other])

    def test_project_page_shows_the_team_and_their_open_tasks(self):
        self.login(self.karim)
        response = self.client.get(f"/projects/{self.project.pk}/")
        team = response.context["team"]
        self.assertEqual(team, [self.omar, self.sara])
        self.assertEqual([person.open_tasks for person in team], [0, 1])  # Sara has "Install the firewall"
        self.assertContains(response, "1 open task")


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


class ManageTeamTests(TeamTestCase):
    """On the project page, the project's manager and the admins add people to the team and remove them."""

    def add(self, person):
        return self.client.post(f"/projects/{self.project.pk}/team/add/", {"person": person.pk}, follow=True)

    def remove(self, person):
        return self.client.post(f"/projects/{self.project.pk}/team/{person.pk}/remove/", follow=True)

    def test_a_login_is_needed(self):
        for address in (f"/projects/{self.project.pk}/team/add/",
                        f"/projects/{self.project.pk}/team/{self.sara.pk}/remove/"):
            with self.subTest(address=address):
                self.assertRedirects(self.client.post(address, {"person": self.karim.pk}),
                                     f"/login/?next={address}")
        self.assertEqual(list(self.project.members.all()), [self.sara])  # unchanged

    def test_who_sees_the_team_buttons(self):
        lina = User.objects.create_user("lina", role="manager")  # a manager, but not of this project
        add_form = f'action="/projects/{self.project.pk}/team/add/"'
        for person, sees_them in [(self.omar, True), (self.amina, True), (self.sara, False), (lina, False)]:
            with self.subTest(person=person.username):
                self.login(person)
                html = self.client.get(f"/projects/{self.project.pk}/").content.decode()
                self.assertEqual(add_form in html, sees_them)
                self.assertEqual("Remove from the team" in html, sees_them)

    def test_the_manager_adds_a_person(self):
        self.login(self.omar)
        response = self.add(self.karim)
        self.assertContains(response, "Karim is now in the team.")
        self.assertEqual(response.context["team"], [self.omar, self.karim, self.sara])
        membership = self.project.memberships.get(user=self.karim)
        self.assertEqual(membership.added_by, self.omar)
        self.assertEqual(membership.history.first().history_user, self.omar)  # for the Activity page

    def test_only_active_people_outside_the_team_can_be_added(self):
        disabled = User.objects.create_user("gone", is_active=False)
        self.login(self.amina)
        form = self.client.get(f"/projects/{self.project.pk}/").context["team_form"]
        self.assertEqual(list(form.fields["person"].queryset), [self.amina, self.karim])
        for person in (self.sara, self.omar, disabled):  # in the team already, the manager, a disabled account
            with self.subTest(person=person.username):
                self.assertContains(self.add(person), "This person can&#x27;t be added")
        self.assertEqual(list(self.project.members.all()), [self.sara])  # nobody was added

    def test_others_cannot_change_the_team(self):
        lina = User.objects.create_user("lina", role="manager")  # a manager, but not of this project
        for person in (self.sara, self.karim, lina):
            with self.subTest(person=person.username):
                self.login(person)
                response = self.client.post(f"/projects/{self.project.pk}/team/add/", {"person": self.karim.pk})
                self.assertEqual(response.status_code, 403)
                response = self.client.post(f"/projects/{self.project.pk}/team/{self.sara.pk}/remove/")
                self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.project.members.all()), [self.sara])  # unchanged
        # Opening the address (instead of sending the form) changes nothing either
        self.login(self.omar)
        self.assertEqual(self.client.get(f"/projects/{self.project.pk}/team/add/").status_code, 405)

    def test_removing_a_person(self):
        self.project.add_to_team(self.karim)
        finished = Task.objects.create(project=self.project, title="Old work", assignee=self.karim, status="done")
        self.login(self.omar)
        response = self.remove(self.karim)
        self.assertContains(response, "Karim is no longer in the team.")
        self.assertEqual(response.context["team"], [self.omar, self.sara])
        finished.refresh_from_db()
        self.assertEqual(finished.assignee, self.karim)  # finished tasks keep the person's name

    def test_someone_with_open_tasks_stays_in_the_team(self):
        self.login(self.omar)
        response = self.remove(self.sara)  # "Install the firewall" isn't done yet
        self.assertContains(response, "Sara Ben still has 1 open task in this project. "
                                      "Give it to someone else (or finish it), then remove Sara Ben from the team.")
        self.assertTrue(self.project.memberships.filter(user=self.sara).exists())

        self.task.status = "done"
        self.task.save()
        self.assertContains(self.remove(self.sara), "Sara Ben is no longer in the team.")

    def test_the_manager_cannot_be_removed(self):
        self.login(self.amina)
        url = f"/projects/{self.project.pk}/team/{self.omar.pk}/remove/"
        self.assertEqual(self.client.post(url).status_code, 404)  # the manager has no line in the team
        ProjectMember.objects.create(project=self.project, user=self.omar)  # even with a line, added by mistake
        self.assertContains(self.remove(self.omar), "Omar Ali is the project&#x27;s manager")
        self.assertEqual(self.project.team(), [self.omar, self.sara])


class AssignInsideTheTeamTests(TeamTestCase):
    """Tasks are given to people in the project's team only.
    Admins can choose anyone: that person then joins the team."""

    def new_task(self, assignee):
        data = {"title": "Configure the VPN", "status": "todo", "priority": "medium", "assignee": assignee.pk}
        return self.client.post(f"/projects/{self.project.pk}/tasks/new/", data, follow=True)

    def test_the_list_shows_the_team_only(self):
        self.login(self.omar)
        field = self.client.get(f"/projects/{self.project.pk}/tasks/new/").context["form"].fields["assignee"]
        self.assertEqual(list(field.queryset), [self.omar, self.sara])

    def test_someone_outside_the_team_is_refused(self):
        self.login(self.omar)
        response = self.new_task(self.karim)
        self.assertContains(response, "Choose someone in the project&#x27;s team.")  # the form comes back
        self.assertFalse(Task.objects.filter(title="Configure the VPN").exists())

    def test_the_person_a_task_is_assigned_to_stays_in_the_list(self):
        # Karim worked here, then left the team (his task was done)
        old = Task.objects.create(project=self.project, title="Old work", assignee=self.karim, status="done")
        self.login(self.omar)
        self.client.post(f"/tasks/{old.pk}/edit/", self.task_form_data(old, title="Old work (2025)"))
        old.refresh_from_db()
        self.assertEqual((old.title, old.assignee), ("Old work (2025)", self.karim))
        self.assertFalse(self.project.memberships.filter(user=self.karim).exists())  # he doesn't come back

    def test_an_admin_can_choose_anyone_and_they_join_the_team(self):
        self.login(self.amina)
        response = self.client.get(f"/projects/{self.project.pk}/tasks/new/")
        self.assertContains(response, '<optgroup label="Not in the team: they will join it">')
        response = self.new_task(self.karim)
        self.assertContains(response, "Karim is now in the team of this project.")
        self.assertEqual(Task.objects.get(title="Configure the VPN").assignee, self.karim)
        self.assertEqual(self.project.memberships.get(user=self.karim).added_by, self.amina)


class ManagerChangeTests(TeamTestCase):
    def setUp(self):
        self.lina = User.objects.create_user("lina", "lina@example.com", role="manager", first_name="Lina")

    def change_manager(self, new_manager):
        data = {"name": "Firewall", "status": "in_progress", "priority": "medium", "manager": new_manager.pk}
        return self.client.post(f"/projects/{self.project.pk}/edit/", data, follow=True)

    def test_the_previous_manager_stays_in_the_team(self):
        self.login(self.amina)
        response = self.change_manager(self.lina)
        self.assertContains(response, "Omar Ali stays in the team, as a member.")
        self.assertEqual(response.context["team"], [self.lina, self.omar, self.sara])
        self.assertEqual(self.project.memberships.get(user=self.omar).added_by, self.amina)

    def test_a_member_who_becomes_the_manager_is_listed_once(self):
        self.project.add_to_team(self.lina)
        self.login(self.amina)
        response = self.change_manager(self.lina)
        self.assertEqual(response.context["team"], [self.lina, self.omar, self.sara])


class AdminPanelTests(TeamTestCase):
    """The admin panel follows the same team rules as the project page."""

    def admin_form(self, **changes):
        """What the admin panel's page of the project sends: the project, its team and its tasks."""
        memberships = list(self.project.memberships.all())
        tasks = list(self.project.tasks.all())
        data = {
            "name": self.project.name, "description": "", "status": self.project.status,
            "priority": self.project.priority, "manager": self.project.manager_id,
            "start_date": "", "end_date": "",
            "memberships-TOTAL_FORMS": len(memberships), "memberships-INITIAL_FORMS": len(memberships),
            "memberships-MIN_NUM_FORMS": 0, "memberships-MAX_NUM_FORMS": 1000,
            "tasks-TOTAL_FORMS": len(tasks), "tasks-INITIAL_FORMS": len(tasks),
            "tasks-MIN_NUM_FORMS": 0, "tasks-MAX_NUM_FORMS": 1000,
        }
        for i, membership in enumerate(memberships):
            data |= {f"memberships-{i}-id": membership.pk, f"memberships-{i}-project": self.project.pk,
                     f"memberships-{i}-user": membership.user_id}
        for i, task in enumerate(tasks):
            data |= {f"tasks-{i}-id": task.pk, f"tasks-{i}-project": self.project.pk,
                     f"tasks-{i}-title": task.title, f"tasks-{i}-status": task.status,
                     f"tasks-{i}-assignee": task.assignee_id or "",
                     f"tasks-{i}-start_date": task.start_date or "", f"tasks-{i}-due_date": task.due_date or ""}
        data.update(changes)
        return data

    def save(self, data):
        self.login(self.amina)
        return self.client.post(f"/admin/projects/project/{self.project.pk}/change/", data)

    def test_the_admin_panel_adds_to_the_team_and_remembers_who_added(self):
        self.login(self.amina)
        self.assertContains(self.client.get(f"/admin/projects/project/{self.project.pk}/change/"),
                            "Team (besides the manager)")
        # A new line in the team, for Karim
        response = self.save(self.admin_form(**{
            "memberships-TOTAL_FORMS": 2, "memberships-1-project": self.project.pk, "memberships-1-user": self.karim.pk,
        }))
        self.assertEqual(response.status_code, 302, response.content.decode()[:3000])
        membership = self.project.memberships.get(user=self.karim)
        self.assertEqual(membership.added_by, self.amina)
        self.assertEqual(membership.history.first().history_type, "+")  # the change history saw it

    def test_someone_with_open_tasks_stays_in_the_team(self):
        response = self.save(self.admin_form(**{"memberships-0-DELETE": "on"}))  # Sara's line
        self.assertContains(response, "Sara Ben still has 1 open task(s) in this project.")
        self.assertTrue(self.project.memberships.filter(user=self.sara).exists())

        self.task.status = "done"
        self.task.save()
        response = self.save(self.admin_form(**{"memberships-0-DELETE": "on"}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.project.memberships.filter(user=self.sara).exists())

    def test_a_saved_line_cannot_become_another_person(self):
        self.save(self.admin_form(**{"memberships-0-user": self.karim.pk}))  # Sara's line
        self.assertEqual(list(self.project.members.all()), [self.sara])

    def test_a_task_given_to_someone_outside_the_team_brings_them_in(self):
        # In the list of tasks below the project
        response = self.save(self.admin_form(**{"tasks-0-assignee": self.karim.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.project.memberships.get(user=self.karim).added_by, self.amina)

        # On the task's own page
        lina = User.objects.create_user("lina", "lina@example.com")
        response = self.client.post(f"/admin/projects/task/{self.task.pk}/change/", {
            "project": self.project.pk, "title": self.task.title, "status": "todo", "priority": "medium",
            "assignee": lina.pk, "due_date": self.task.due_date, "position": 0,
            "comments-TOTAL_FORMS": 0, "comments-INITIAL_FORMS": 0,
            "attachments-TOTAL_FORMS": 0, "attachments-INITIAL_FORMS": 0,
        })
        self.assertEqual(response.status_code, 302, response.content.decode()[:3000])
        self.assertEqual(self.project.memberships.get(user=lina).added_by, self.amina)

    def test_a_new_manager_keeps_the_previous_one_in_the_team(self):
        lina = User.objects.create_user("lina", "lina@example.com", role="manager", first_name="Lina")
        response = self.save(self.admin_form(manager=lina.pk))
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.team(), [lina, self.omar, self.sara])
        self.assertEqual(self.project.memberships.get(user=self.omar).added_by, self.amina)


class TeamActivityTests(TeamTestCase):
    def test_team_changes_are_in_the_activity_feed(self):
        self.login(self.omar)
        self.client.post(f"/projects/{self.project.pk}/team/add/", {"person": self.karim.pk})
        self.client.post(f"/projects/{self.project.pk}/team/{self.karim.pk}/remove/")

        text = visible_text(self.client.get("/activity/"))
        removed = text.find("Omar Ali removed Karim from the team of Firewall")
        added = text.find("Omar Ali added Karim to the team of Firewall")
        self.assertNotEqual(removed, -1)
        self.assertNotEqual(added, -1)
        self.assertLess(removed, added)  # newest first

        self.assertIn("removed Karim from the team", visible_text(self.client.get(f"/projects/{self.project.pk}/")))
        # The project page shows its own team only
        wifi = Project.objects.create(name="Wi-Fi", manager=self.omar)
        wifi.add_to_team(self.sara)
        self.assertNotIn("the team of Wi-Fi", visible_text(self.client.get(f"/projects/{self.project.pk}/")))
        self.assertIn("the team of Wi-Fi", visible_text(self.client.get(f"/projects/{wifi.pk}/")))

        self.assertIn("removed Karim from the team", visible_text(self.client.get(f"/activity/?person={self.omar.pk}")))
        self.assertNotIn("Karim from the team", visible_text(self.client.get(f"/activity/?person={self.sara.pk}")))
        self.assertNotIn("to the team", visible_text(self.client.get(f"/activity/?task={self.task.pk}")))

    def test_team_changes_of_a_deleted_project_are_not_shown(self):
        other = Project.objects.create(name="Wi-Fi", manager=self.omar)
        other.add_to_team(self.karim)
        other.delete()
        self.login(self.amina)
        text = visible_text(self.client.get("/activity/"))
        self.assertIn("deleted the project Wi-Fi", text)
        self.assertNotIn("Karim to the team", text)
        self.assertNotIn("Karim from the team", text)
