"""The read-only executive role (release 1.2): sees every project, team, person, figure and activity; changes nothing;
is never a manager, a team member or given tasks. The every-address matrix of test_permissions.py has its column too."""

import re

from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import URLPattern, URLResolver, get_resolver

from accounts.middleware import ReadOnlyRoleMiddleware
from accounts.models import User
from administration.forms import AccountForm, AddToProjectForm
from projects.forms import ProjectForm, TaskForm, TeamMemberForm
from projects.models import Comment, Notification, Project, ProjectMember, Task

from .base import TeamTestCase, days_from_today


class ExecutiveTestCase(TeamTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.eva = User.objects.create_user("eva", "eva@example.com", first_name="Eva", role=User.Role.EXECUTIVE)
        # A project Eva isn't part of (nobody is, but its manager): she sees it anyway
        cls.wifi = Project.objects.create(name="Wi-Fi", manager=cls.karim, status="in_progress")
        cls.survey = Task.objects.create(project=cls.wifi, title="Survey the floors", assignee=cls.karim,
                                         due_date=days_from_today(-2))

    def setUp(self):
        self.login(self.eva)


class SeesEverythingTests(ExecutiveTestCase):
    def test_every_project_and_task(self):
        listed = {p.name for p in self.client.get("/projects/?status=all").context["projects"]}
        self.assertEqual(listed, {"Firewall", "Wi-Fi"})
        for address in (f"/projects/{self.wifi.pk}/", f"/projects/{self.wifi.pk}/board/",
                        f"/projects/{self.wifi.pk}/timeline/", f"/projects/{self.wifi.pk}/report/",
                        f"/tasks/{self.survey.pk}/", f"/projects/{self.wifi.pk}/export.xlsx"):
            with self.subTest(address=address):
                self.assertEqual(self.client.get(address).status_code, 200)

    def test_every_person_and_their_figures(self):
        for person in (self.sara, self.karim, self.amina):
            response = self.client.get(f"/people/{person.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "In all projects")  # full figures, like an admin

    def test_no_figures_on_an_executives_own_page(self):
        response = self.client.get(f"/people/{self.eva.pk}/")
        self.assertNotContains(response, 'id="contribution"')
        self.assertContains(response, "Executive (read-only)")

    def test_the_numbers_count_everything(self):
        self.assertEqual(self.client.get("/").context["active_projects"], 2)
        response = self.client.get("/dashboard/")
        self.assertContains(response, "The big picture across all active projects.")
        self.assertIn(self.wifi, response.context["needs_attention"])  # its task is overdue

    def test_the_activity_of_every_project(self):
        self.login(self.karim)
        self.client.post(f"/tasks/{self.survey.pk}/comments/add/", {"body": "Started"})
        self.login(self.eva)
        response = self.client.get("/activity/")
        self.assertContains(response, "Survey the floors")


class ChangesNothingTests(ExecutiveTestCase):
    def all_addresses(self):
        """Every address of the app (outside Django's admin panel), with made-up numbers in it."""
        def walk(patterns, prefix=""):
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    if pattern.app_name != "admin":
                        yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
                elif isinstance(pattern, URLPattern):
                    route = prefix + str(pattern.pattern)
                    yield pattern.name, "/" + re.sub(r"<(int:)?(\w+:)?\w+>", "1", route)

        return list(walk(get_resolver().url_patterns))

    def test_every_change_is_refused_except_their_own_account(self):
        allowed = ReadOnlyRoleMiddleware.ALLOWED | {"login", "healthz"}
        checked = 0
        for name, address in self.all_addresses():
            if name in allowed:
                continue
            with self.subTest(name=name, address=address):
                response = self.client.post(address, {})
                self.assertEqual(response.status_code, 403)
                checked += 1
        self.assertGreater(checked, 40)

    def test_the_refusal_says_why_and_changes_nothing(self):
        before = (Comment.objects.count(), Task.objects.get(pk=self.task.pk).status)
        response = self.client.post(f"/tasks/{self.task.pk}/comments/add/", {"body": "Hello"})
        self.assertContains(response, "Your role can only read: nothing was changed.", status_code=403)
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "done"})
        self.client.post(f"/tasks/{self.task.pk}/move/", {"status": "done", "order": [self.task.pk]})
        self.assertEqual((Comment.objects.count(), Task.objects.get(pk=self.task.pk).status), before)

    def test_their_own_account_still_works(self):
        self.assertEqual(self.client.post("/language/", {"language": "fr", "next": "/"}).status_code, 302)
        self.assertEqual(self.client.post("/profile/notifications/", {"email_reminder": "on"}).status_code, 302)
        self.assertEqual(self.client.post("/notifications/read-all/").status_code, 302)
        self.assertEqual(self.client.post("/feedback/", {"rating": 5, "kind": "praise"}).status_code, 302)
        # Their own profile (phone, picture, language: the names belong to the admins)
        self.assertEqual(self.client.post("/profile/", {"phone": "+222 22 12 34 56", "language": "en"}).status_code, 302)
        self.eva.refresh_from_db()
        self.assertEqual(self.eva.phone, "+222 22 12 34 56")
        self.assertEqual(self.client.post("/logout/").status_code, 302)

    def test_the_pages_show_no_change_buttons(self):
        task_page = self.client.get(f"/tasks/{self.task.pk}/").content.decode()
        for control in ('action="/tasks/%d/comments/add/"' % self.task.pk, 'action="/tasks/%d/files/add/"' % self.task.pk,
                        f'action="/tasks/{self.task.pk}/status/"', f'href="/tasks/{self.task.pk}/edit/"'):
            self.assertNotIn(control, task_page)
        project_page = self.client.get(f"/projects/{self.project.pk}/").content.decode()
        self.assertNotIn(f'href="/projects/{self.project.pk}/edit/"', project_page)
        self.assertNotIn(f'action="/projects/{self.project.pk}/team/add/"', project_page)
        self.assertNotIn('href="/projects/new/"', self.client.get("/projects/").content.decode())
        board = self.client.get(f"/projects/{self.project.pk}/board/").content.decode()
        self.assertNotIn("board-card movable", board)


class GivenNothingToDoTests(ExecutiveTestCase):
    def test_never_in_the_apps_lists_of_people(self):
        self.assertNotIn(self.eva, ProjectForm(user=self.amina).fields["manager"].queryset)
        self.assertNotIn(self.eva, TaskForm(project=self.project, user=self.amina).fields["assignee"].queryset)
        self.assertNotIn(self.eva, TeamMemberForm(project=self.project).fields["person"].queryset)

    def test_never_added_to_a_project_in_administration(self):
        form = AddToProjectForm({"project": self.wifi.pk}, person=self.eva)
        self.assertFalse(form.is_valid())
        self.assertIn("An executive reads every project but is in no team.", form.non_field_errors())
        self.login(self.amina)
        self.assertIsNone(self.client.get(f"/administration/users/{self.eva.pk}/").context["add_form"])
        self.client.post(f"/administration/users/{self.eva.pk}/projects/add/", {"project": self.wifi.pk})
        self.assertFalse(ProjectMember.objects.filter(user=self.eva).exists())

    def test_never_in_the_admin_panels_lists_of_people(self):
        self.login(self.amina)
        option = f'<option value="{self.eva.pk}"'
        for address in (f"/admin/projects/project/{self.project.pk}/change/", f"/admin/projects/task/{self.task.pk}/change/",
                        "/admin/projects/project/add/", "/admin/projects/task/add/"):
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, option)

    def test_not_allowed_in_the_admin_panel_even_with_staff_status(self):
        User.objects.filter(pk=self.eva.pk).update(is_staff=True)
        response = self.client.get("/admin/")
        self.assertRedirects(response, "/admin/login/?next=/admin/", fetch_redirect_response=False)

    def test_no_email_and_no_notification(self):
        mail.outbox = []
        call_command("send_reminders", stdout=__import__("io").StringIO())
        self.assertFalse([message for message in mail.outbox if "eva@example.com" in message.to])
        self.assertFalse(Notification.objects.filter(recipient=self.eva).exists())


class RoleChangeTests(ExecutiveTestCase):
    def account_form(self, person, role, acting=None):
        data = {"username": person.username, "first_name": person.first_name, "last_name": person.last_name or "Test",
                "email": person.email or f"{person.username}@example.com", "phone": "", "role": role, "language": "en"}
        return AccountForm(data, instance=User.objects.get(pk=person.pk), acting_user=acting or self.amina)

    def test_someone_with_work_to_hand_over_is_refused(self):
        form = self.account_form(self.omar, "executive")  # manages Firewall
        self.assertFalse(form.is_valid())
        self.assertIn("Omar Ali can't become an executive yet: they manage 1 project(s). Give them to someone else first.",
                      form.errors["role"])
        form = self.account_form(self.sara, "executive")  # has an open task
        self.assertIn("they have 1 open task(s)", form.errors["role"][0])
        ProjectMember.objects.filter(project=self.project, user=self.sara).update(is_manager=True)
        self.assertIn("they co-manage 1 project(s)", self.account_form(self.sara, "executive").errors["role"][0])

    def test_someone_with_nothing_to_hand_over_becomes_an_executive(self):
        lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina")
        ProjectMember.objects.create(project=self.project, user=lina)  # a plain member: allowed
        form = self.account_form(lina, "executive")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(User.objects.get(pk=lina.pk).is_executive)

    def test_back_to_user_is_free(self):
        form = self.account_form(self.eva, "user")
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_superuser_stays_an_admin(self):
        self.login(self.amina)
        other_admin = User.objects.create_superuser("root2", "root2@example.com")
        response = self.client.post(f"/admin/accounts/user/{other_admin.pk}/change/", {
            "username": "root2", "email": "root2@example.com", "role": "executive", "is_superuser": "on",
            "is_staff": "on", "is_active": "on", "language": "en", "date_joined_0": "2026-01-01", "date_joined_1": "10:00"})
        self.assertContains(response, "it is always an admin")
        self.assertTrue(User.objects.get(pk=other_admin.pk).is_admin)

    def test_the_admin_panel_refuses_a_manager_too(self):
        self.login(self.amina)
        response = self.client.post(f"/admin/accounts/user/{self.omar.pk}/change/", {
            "username": "omar", "first_name": "Omar", "last_name": "Ali", "email": "omar@example.com",
            "role": "executive", "is_active": "on", "language": "en", "date_joined_0": "2026-01-01",
            "date_joined_1": "10:00"})
        self.assertContains(response, "can&#x27;t become an executive yet")
        self.assertEqual(User.objects.get(pk=self.omar.pk).role, "user")


class PeopleOverviewTests(ExecutiveTestCase):
    def test_executives_and_admins_only(self):
        self.assertEqual(self.client.get("/people/overview/").status_code, 200)
        self.login(self.amina)
        self.assertEqual(self.client.get("/people/overview/").status_code, 200)
        self.login(self.omar)
        self.assertEqual(self.client.get("/people/overview/").status_code, 403)

    def test_executives_are_not_listed(self):
        rows = self.client.get("/people/overview/").context["rows"]
        self.assertNotIn(self.eva, [person for person, _ in rows])
        self.assertIn(self.sara, [person for person, _ in rows])

    def test_the_old_address_leads_there(self):
        self.assertRedirects(self.client.get("/administration/people/"), "/people/overview/")

    def test_in_the_menu_for_executives_and_admins(self):
        self.assertContains(self.client.get("/"), 'href="/people/overview/"')
        self.login(self.sara)
        self.assertNotContains(self.client.get("/"), 'href="/people/overview/"')


class LanguageTests(ExecutiveTestCase):
    def test_in_french_and_arabic(self):
        for language, role, refusal in (
                ("fr", "Direction (lecture seule)", "Votre rôle ne permet que la lecture : rien n&#x27;a été modifié."),
                ("ar", "الإدارة العليا (قراءة فقط)", "دورك يسمح بالقراءة فقط: لم يتغير شيء.")):
            with self.subTest(language=language):
                User.objects.filter(pk=self.eva.pk).update(language=language)
                self.assertContains(self.client.get(f"/people/{self.eva.pk}/"), role)
                response = self.client.post(f"/tasks/{self.task.pk}/comments/add/", {"body": "x"})
                self.assertContains(response, refusal, status_code=403)
        self.assertContains(self.client.get("/"), 'dir="rtl"')


class MigrationTests(TransactionTestCase):
    """accounts/0008 only adds a choice: going from 0007 to 0008, every account keeps its role."""

    def test_from_0007_to_0008_every_role_is_kept(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("accounts", "0007_email_preferences")])
        old_user = executor.loader.project_state([("accounts", "0007_email_preferences")]).apps.get_model("accounts", "User")
        old_user.objects.create(username="old-admin", role="admin")
        old_user.objects.create(username="old-user", role="user")
        old_user.objects.create(username="old-default")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        roles = dict(User.objects.filter(username__startswith="old-").values_list("username", "role"))
        self.assertEqual(roles, {"old-admin": "admin", "old-user": "user", "old-default": "user"})
        self.assertFalse(User.objects.filter(role="executive").exists())
