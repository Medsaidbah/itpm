"""Co-managers (Step 21): the project's manager can give the manager role to a team member.
The co-manager has the manager's rights on the project (edit it, its tasks and its team) but cannot
delete it, change its main manager, or give the role to others. Only the main manager and admins
can take the role away; a co-manager may give up their own."""

from accounts.models import User
from projects.emails import reminder_for
from projects.models import Project, ProjectMember, Task

from .base import TeamTestCase, days_from_today
from .test_activity import visible_text


class CoManagerTestCase(TeamTestCase):
    def setUp(self):
        # Lina is in the team of "Firewall" (with Sara); Omar is the main manager
        self.lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina", last_name="Diallo")
        self.project.add_to_team(self.lina)
        self.url = f"/projects/{self.project.pk}/"

    def give_role(self, who, person, value="1"):
        self.login(who)
        return self.client.post(f"{self.url}team/{person.pk}/manager/", {"manager": value})

    def refreshed(self):
        return Project.objects.get(pk=self.project.pk)


class GivingTheRoleTests(CoManagerTestCase):
    def test_the_manager_gives_the_role_to_a_team_member(self):
        response = self.give_role(self.omar, self.lina)
        self.assertRedirects(response, self.url)
        self.assertTrue(self.refreshed().is_co_manager(self.lina))
        self.assertEqual(self.refreshed().manager, self.omar)  # Omar is still the main manager

    def test_an_admin_can_too(self):
        self.give_role(self.amina, self.lina)
        self.assertTrue(self.refreshed().is_co_manager(self.lina))

    def test_members_outsiders_and_co_managers_cannot_give_the_role(self):
        self.give_role(self.omar, self.lina)  # Lina is a co-manager now
        for who, status in ((self.sara, 403), (self.lina, 403), (self.karim, 404)):
            with self.subTest(who=who.username):
                self.assertEqual(self.give_role(who, self.sara).status_code, status)
        self.assertFalse(self.refreshed().is_co_manager(self.sara))

    def test_only_team_members_can_be_given_the_role(self):
        self.login(self.omar)
        response = self.client.post(f"{self.url}team/{self.karim.pk}/manager/", {"manager": "1"})
        self.assertEqual(response.status_code, 404)  # Karim isn't in the team
        self.assertFalse(ProjectMember.objects.filter(user=self.karim).exists())

    def test_the_main_manager_cannot_be_made_a_co_manager(self):
        ProjectMember.objects.create(project=self.project, user=self.omar)  # an old line, ignored
        self.give_role(self.amina, self.omar)
        self.assertNotIn(self.omar.pk, self.refreshed().co_manager_ids())
        self.assertFalse(ProjectMember.objects.get(project=self.project, user=self.omar).is_manager)  # nothing saved

    def test_a_deactivated_person_cannot_be_given_the_role(self):
        User.objects.filter(pk=self.lina.pk).update(is_active=False)
        self.give_role(self.omar, self.lina)
        self.assertFalse(self.refreshed().is_co_manager(self.lina))

    def test_a_get_request_does_nothing(self):
        self.login(self.omar)
        self.assertEqual(self.client.get(f"{self.url}team/{self.lina.pk}/manager/").status_code, 405)


class WhatACoManagerCanDoTests(CoManagerTestCase):
    def setUp(self):
        super().setUp()
        self.give_role(self.omar, self.lina)
        self.login(self.lina)

    def test_edits_the_project_its_tasks_and_its_team(self):
        project_data = {"name": "Firewall v2", "status": "in_progress", "priority": "medium", "manager": self.omar.pk}
        self.assertRedirects(self.client.post(f"{self.url}edit/", project_data), self.url)
        self.assertEqual(self.refreshed().name, "Firewall v2")

        response = self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(title="Install it"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Task.objects.get(pk=self.task.pk).title, "Install it")

        self.assertEqual(self.client.get(f"{self.url}tasks/new/").status_code, 200)
        self.client.post(f"{self.url}team/add/", {"person": self.karim.pk})
        self.assertTrue(self.project.memberships.filter(user=self.karim).exists())
        move = {"status": "in_progress", "order": [self.task.pk]}
        response = self.client.post(f"/tasks/{self.task.pk}/move/", move)
        self.assertEqual(response.status_code, 200)

    def test_cannot_delete_the_project_or_change_its_main_manager(self):
        self.assertEqual(self.client.get(f"{self.url}delete/").status_code, 403)
        # The manager field is switched off for them: what they send for it is ignored
        data = {"name": "Firewall", "status": "in_progress", "priority": "medium", "manager": self.lina.pk}
        self.client.post(f"{self.url}edit/", data)
        self.assertEqual(self.refreshed().manager, self.omar)

    def test_the_team_page_names_the_role_and_offers_the_buttons_only_to_those_who_may(self):
        text = visible_text(self.client.get(self.url))
        self.assertIn("Co-manager", text)
        page = self.client.get(self.url).content.decode()
        # Lina may give up her own role, but not give the role to Sara
        self.assertIn(f"/team/{self.lina.pk}/manager/", page)
        self.assertNotIn(f"/team/{self.sara.pk}/manager/", page)
        self.login(self.omar)
        page = self.client.get(self.url).content.decode()
        self.assertIn(f"/team/{self.sara.pk}/manager/", page)
        self.assertNotIn(f"/team/{self.omar.pk}/manager/", page)  # the main manager has no button

    def test_she_may_give_up_her_own_role(self):
        self.give_role(self.lina, self.lina, "0")
        self.assertFalse(self.refreshed().is_co_manager(self.lina))
        self.assertEqual(self.client.get(f"{self.url}edit/").status_code, 403)

    def test_she_cannot_take_the_role_from_another_co_manager(self):
        self.give_role(self.omar, self.sara)
        self.assertEqual(self.give_role(self.lina, self.sara, "0").status_code, 403)
        self.assertTrue(self.refreshed().is_co_manager(self.sara))

    def test_the_main_manager_and_admins_take_the_role_away(self):
        self.give_role(self.omar, self.lina, "0")
        self.assertFalse(self.refreshed().is_co_manager(self.lina))
        self.give_role(self.omar, self.lina)
        self.give_role(self.amina, self.lina, "0")
        self.assertFalse(self.refreshed().is_co_manager(self.lina))

    def test_a_co_manager_must_lose_the_role_before_leaving_the_team(self):
        self.login(self.omar)
        self.client.post(f"{self.url}team/{self.lina.pk}/remove/")
        self.assertTrue(self.project.memberships.filter(user=self.lina).exists())  # still there
        self.give_role(self.omar, self.lina, "0")
        self.client.post(f"{self.url}team/{self.lina.pk}/remove/")
        self.assertFalse(self.project.memberships.filter(user=self.lina).exists())

    def test_the_rights_are_for_this_project_only(self):
        other = Project.objects.create(name="Wi-Fi", manager=self.karim)
        other.add_to_team(self.lina)
        self.assertEqual(self.client.get(f"/projects/{other.pk}/edit/").status_code, 403)

    def test_checking_the_rights_asks_the_database_once_per_project(self):
        project = self.refreshed()
        with self.assertNumQueries(1):
            for _ in range(5):
                project.can_edit(self.lina)
                project.can_edit(self.sara)


class ChangingTheMainManagerTests(CoManagerTestCase):
    def test_a_co_manager_who_becomes_the_main_manager_does_not_come_back_as_co_manager(self):
        self.give_role(self.omar, self.lina)
        project = self.refreshed()
        project.manager = self.lina  # an admin chose Lina as the main manager
        project.save()
        self.assertEqual(self.refreshed().manager, self.lina)
        self.assertNotIn(self.lina.pk, self.refreshed().co_manager_ids())
        # ... and later, when somebody else is chosen, Lina is an ordinary member
        project = self.refreshed()
        project.manager = self.omar
        project.save()
        self.assertFalse(self.refreshed().is_co_manager(self.lina))

    def test_the_previous_main_manager_stays_an_ordinary_member(self):
        project = self.refreshed()
        project.manager = self.lina
        project.save()
        self.assertTrue(self.project.memberships.filter(user=self.omar, is_manager=False).exists())
        self.login(self.omar)
        self.assertEqual(self.client.get(f"{self.url}edit/").status_code, 403)


class AroundTheRoleTests(CoManagerTestCase):
    def test_visibility_is_unchanged(self):
        self.give_role(self.omar, self.lina)
        wifi = Project.objects.create(name="Wi-Fi", manager=self.karim)
        self.assertEqual(set(Project.objects.visible_to(self.lina)), {self.project})
        self.assertNotIn(wifi, Project.objects.visible_to(self.lina))

    def test_the_activity_page_tells_who_gave_and_took_the_role(self):
        self.give_role(self.omar, self.lina)
        self.give_role(self.omar, self.lina, "0")
        text = visible_text(self.client.get(f"/activity/?project={self.project.pk}"))
        self.assertIn("Omar Ali made Lina Diallo a co-manager of Firewall", text)
        self.assertIn("Omar Ali took the manager role from Lina Diallo in Firewall", text)
        # newest first
        self.assertLess(text.index("took the manager role"), text.index("made Lina Diallo a co-manager"))

    def test_saving_a_member_without_a_role_change_is_not_shown(self):
        membership = self.project.memberships.get(user=self.lina)
        membership.save()
        self.login(self.omar)
        text = visible_text(self.client.get(f"/activity/?project={self.project.pk}"))
        self.assertNotIn("co-manager", text)

    def test_co_managers_also_get_the_late_tasks_of_the_project_in_their_reminder(self):
        Task.objects.create(project=self.project, title="Late one", assignee=self.sara, due_date=days_from_today(-3))
        before = {s.title for s in reminder_for(self.lina, days_from_today(0))["sections"]}
        self.assertNotIn("Late in projects you manage", before)
        self.give_role(self.omar, self.lina)
        reminder = reminder_for(self.lina, days_from_today(0))
        sections = {s.title: [t.title for t in s.tasks] for s in reminder["sections"]}
        self.assertEqual(sections["Late in projects you manage"], ["Late one"])

    def test_a_reminder_does_not_repeat_a_task_for_a_manager_who_is_also_a_co_manager(self):
        # Defensive: an old member line with the mark, while the person is the main manager
        ProjectMember.objects.create(project=self.project, user=self.omar, is_manager=True)
        Task.objects.create(project=self.project, title="Late one", assignee=self.sara, due_date=days_from_today(-3))
        reminder = reminder_for(self.omar, days_from_today(0))
        sections = {s.title: [t.title for t in s.tasks] for s in reminder["sections"]}
        self.assertEqual(sections["Late in projects you manage"], ["Late one"])

    def test_the_migration_leaves_existing_teams_without_the_role(self):
        # Everybody who was in a team before this step is an ordinary member
        self.assertFalse(ProjectMember.objects.filter(is_manager=True).exists())
