"""Tests of Stage 17b: the Administration section (accounts, a person's projects, locked logins)
and deleting a project. They start from the same small team as the other tests:
Amina (admin); Omar, who manages the project "Firewall"; Sara, in its team (the task is hers);
and Karim, outside the team. See projects/tests/base.py."""

import shutil
import tempfile
from datetime import timedelta

from axes.models import AccessAttempt
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import model_to_dict
from django.test import Client, override_settings
from django.utils import timezone
from PIL import Image

from accounts.forms import UserAdminChangeForm
from accounts.models import User, leaves_no_active_admin
from accounts.pictures import PICTURE_SIZE
from projects.models import Attachment, Comment, Project, ProjectMember, Task
from projects.tests.base import TeamTestCase, picture_file
from projects.tests.test_activity import visible_text

from .logins import failed_logins


def messages_in(response):
    """The short messages shown at the top of the page, like "Changes saved." """
    return [str(message) for message in response.context["messages"]]


def wrong_passwords(username, ip_address="10.0.0.1", failures=5, minutes_ago=5, browser="Firefox"):
    """Writes what django-axes writes after wrong passwords (without typing them)."""
    attempt = AccessAttempt.objects.create(
        username=username, ip_address=ip_address, user_agent=browser, get_data="", post_data="",
        http_accept="text/html", path_info="/login/", failures_since_start=failures,
    )
    AccessAttempt.objects.filter(pk=attempt.pk).update(attempt_time=timezone.now() - timedelta(minutes=minutes_ago))


class MediaFolderMixin:
    """Pictures and files are saved in a temporary folder, not in the real media folder."""

    def setUp(self):
        super().setUp()
        media_folder = tempfile.mkdtemp()
        settings_change = override_settings(MEDIA_ROOT=media_folder)
        settings_change.enable()
        self.addCleanup(settings_change.disable)
        self.addCleanup(shutil.rmtree, media_folder, ignore_errors=True)


class AccessTests(TeamTestCase):
    """Only admins open the section, whatever the address typed. (The list of every address
    of the app, with who gets what, is in projects/tests/test_permissions.py.)"""

    def pages(self):
        s = self.sara.pk
        return ["/administration/", "/administration/users/", "/administration/users/new/",
                f"/administration/users/{s}/", f"/administration/users/{s}/password/",
                f"/administration/users/{s}/deactivate/", "/administration/projects/", "/administration/tasks/",
                "/administration/logins/"]

    def test_only_admins_open_the_pages(self):
        for page in self.pages():
            with self.subTest(page=page):
                self.client.logout()
                self.assertRedirects(self.client.get(page), f"/login/?next={page}")
                for person in (self.omar, self.sara, self.karim):
                    self.login(person)
                    self.assertEqual(self.client.get(page).status_code, 403)
                self.login(self.amina)
                self.assertEqual(self.client.get(page).status_code, 200)

    def test_others_cannot_change_anything_by_sending_forms(self):
        s, p = self.sara.pk, self.project.pk
        wrong_passwords("sara")
        self.login(self.omar)  # he manages a project, but isn't an admin
        sent = [
            (f"/administration/users/{s}/", {"username": "sara", "first_name": "Hacked", "last_name": "Ben",
                                             "email": "sara@example.com", "role": "admin"}),
            ("/administration/users/new/", {"username": "mole", "first_name": "M", "last_name": "M",
                                            "email": "m@example.com", "role": "admin",
                                            "password1": "Very-long-pass-91", "password2": "Very-long-pass-91"}),
            (f"/administration/users/{s}/password/", {"new_password1": "Very-long-pass-91",
                                                      "new_password2": "Very-long-pass-91"}),
            (f"/administration/users/{s}/deactivate/", {}),
            (f"/administration/users/{self.karim.pk}/reactivate/", {}),
            (f"/administration/users/{self.karim.pk}/projects/add/", {"project": p}),
            (f"/administration/users/{s}/projects/{p}/remove/", {}),
            ("/administration/logins/unlock/", {"username": "sara"}),
        ]
        User.objects.filter(pk=self.karim.pk).update(is_active=False)
        for address, data in sent:
            with self.subTest(address=address):
                self.assertEqual(self.client.post(address, data).status_code, 403)
        self.sara.refresh_from_db()
        self.assertEqual((self.sara.first_name, self.sara.role, self.sara.is_active), ("Sara", "user", True))
        self.assertFalse(self.sara.check_password("Very-long-pass-91"))
        self.assertFalse(User.objects.filter(username="mole").exists())
        self.assertFalse(User.objects.get(pk=self.karim.pk).is_active)
        self.assertEqual(set(self.project.team_queryset()), {self.omar, self.sara})
        self.assertTrue(AccessAttempt.objects.filter(username="sara").exists())

    def test_the_menu_item_is_for_admins_only(self):
        self.login(self.sara)
        self.assertNotContains(self.client.get("/"), 'href="/administration/"')
        self.login(self.amina)
        self.assertContains(self.client.get("/"), 'href="/administration/"')

    def test_admins_go_from_a_persons_page_to_their_account(self):
        self.login(self.omar)  # Sara is in his team: he sees her page, without the admin button
        self.assertNotContains(self.client.get(f"/people/{self.sara.pk}/"), f"/administration/users/{self.sara.pk}/")
        self.login(self.amina)
        self.assertContains(self.client.get(f"/people/{self.sara.pk}/"), f"/administration/users/{self.sara.pk}/")


class OverviewAndListTests(TeamTestCase):
    def setUp(self):
        self.login(self.amina)

    def listed(self, query=""):
        return [person.username for person in self.client.get(f"/administration/users/{query}").context["people"]]

    def test_the_overview_counts(self):
        User.objects.filter(pk=self.karim.pk).update(is_active=False)
        wrong_passwords("sara")
        context = self.client.get("/administration/").context
        self.assertEqual(
            (context["active_count"], context["deactivated_count"], context["admin_count"], context["locked_count"]),
            (3, 1, 1, 1),
        )
        self.assertEqual(context["missing_count"], 1)  # Amina has no last name (Karim is deactivated)

    def test_each_person_with_their_projects_and_open_tasks(self):
        people = {person.username: person for person in self.client.get("/administration/users/").context["people"]}
        self.assertEqual(list(people), ["amina", "karim", "omar", "sara"])  # sorted by name
        self.assertEqual((people["omar"].project_count, people["omar"].open_task_count), (1, 0))
        self.assertEqual((people["sara"].project_count, people["sara"].open_task_count), (1, 1))
        self.assertEqual((people["karim"].project_count, people["karim"].has_missing_details), (0, True))
        # A member who became the manager is counted once
        ProjectMember.objects.create(project=self.project, user=self.omar)
        omar = next(p for p in self.client.get("/administration/users/").context["people"] if p.username == "omar")
        self.assertEqual(omar.project_count, 1)

    def test_search_and_filters(self):
        User.objects.filter(pk=self.karim.pk).update(is_active=False, phone="+222 44 55 66 77")
        self.assertEqual(self.listed("?q=sara ben"), ["sara"])  # first and last name together
        self.assertEqual(self.listed("?q=OMAR@EXAMPLE"), ["omar"])  # email, in capitals or not
        self.assertEqual(self.listed("?q=44 55&status=all"), ["karim"])  # phone
        self.assertEqual(self.listed("?status=deactivated"), ["karim"])
        self.assertEqual(self.listed(), ["amina", "omar", "sara"])  # active accounts by default
        self.assertEqual(self.listed("?role=admin"), ["amina"])
        self.assertEqual(self.listed("?missing=1&status=all"), ["amina", "karim"])  # no last name
        self.assertContains(self.client.get("/administration/users/?q=nobody"), "No account matches this search.")

    def test_a_locked_login_is_shown_in_the_list(self):
        wrong_passwords("sara")
        people = {person.username: person for person in self.client.get("/administration/users/").context["people"]}
        self.assertTrue(people["sara"].is_locked)
        self.assertFalse(people["omar"].is_locked)


# Saving a password is slow on purpose: these tests use a fast method instead (only in tests)
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NewAccountTests(MediaFolderMixin, TeamTestCase):
    def new_account(self, **changes):
        data = {"username": "lina", "first_name": "Lina", "last_name": "Diallo", "email": "lina@example.com",
                "phone": "+222 22 33 44 55", "role": "user",
                "password1": "Blue-river-2026", "password2": "Blue-river-2026"}
        data.update(changes)
        return self.client.post("/administration/users/new/", data, follow=True)

    def setUp(self):
        super().setUp()
        self.login(self.amina)

    def test_creating_an_account(self):
        response = self.new_account(picture=picture_file())
        lina = User.objects.get(username="lina")
        self.assertRedirects(response, f"/administration/users/{lina.pk}/")
        self.assertIn("The account of Lina Diallo is created.", messages_in(response)[0])
        self.assertEqual((lina.get_full_name(), lina.email, lina.phone, lina.role, lina.is_active),
                         ("Lina Diallo", "lina@example.com", "+222 22 33 44 55", "user", True))
        self.assertTrue(lina.check_password("Blue-river-2026"))
        with Image.open(lina.picture.path) as image:  # made small and square, like on the profile page
            self.assertEqual(image.size, (PICTURE_SIZE, PICTURE_SIZE))
        # The new person can log in
        self.assertRedirects(Client().post("/login/", {"username": "lina", "password": "Blue-river-2026"}), "/")

    def test_an_admin_account(self):
        self.new_account(role="admin")
        self.assertTrue(User.objects.get(username="lina").is_admin)

    def test_names_and_email_are_needed(self):
        response = self.new_account(first_name="", last_name="", email="")
        for field in ("first_name", "last_name", "email"):
            self.assertEqual(response.context["form"].errors[field], ["This field is required."])
        self.assertFalse(User.objects.filter(username="lina").exists())

    def test_usernames_and_emails_are_not_shared(self):
        response = self.new_account(username="SARA", email="SARA@example.com")
        errors = response.context["form"].errors
        self.assertEqual(errors["username"], ["A user with that username already exists."])
        self.assertEqual(errors["email"], ["Another account already uses this email address."])
        self.assertEqual(User.objects.filter(email__iexact="sara@example.com").count(), 1)

    def test_weak_or_different_passwords_are_refused(self):
        response = self.new_account(password1="12345678", password2="12345678")
        self.assertIn("This password is entirely numeric.", response.context["form"].errors["password2"])
        response = self.new_account(password2="Another-pass-2026")
        self.assertIn("The two password fields didn’t match.", response.context["form"].errors["password2"])
        self.assertFalse(User.objects.filter(username="lina").exists())


class AccountDetailsTests(MediaFolderMixin, TeamTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.amina)

    def save_details(self, person, **changes):
        data = {"username": person.username, "first_name": person.first_name, "last_name": person.last_name,
                "email": person.email, "phone": person.phone, "role": person.role}
        data.update(changes)
        return self.client.post(f"/administration/users/{person.pk}/", data, follow=True)

    def test_an_admin_changes_everything(self):
        response = self.save_details(self.sara, username="sara.ben", first_name="Sarah", email="sarah@example.com",
                                     phone="+222 22 12 34 56", role="admin", picture=picture_file())
        self.assertEqual(messages_in(response), ["Changes saved."])
        self.sara.refresh_from_db()
        self.assertEqual((self.sara.username, self.sara.first_name, self.sara.email, self.sara.phone, self.sara.role),
                         ("sara.ben", "Sarah", "sarah@example.com", "+222 22 12 34 56", "admin"))
        self.assertTrue(self.sara.picture)

        self.save_details(self.sara, role="user", remove_picture="on")
        self.sara.refresh_from_db()
        self.assertEqual((self.sara.role, bool(self.sara.picture)), ("user", False))

    def test_mistakes_are_shown_and_nothing_is_saved(self):
        response = self.save_details(self.sara, first_name="", last_name="Benali", email="omar@example.com",
                                     phone="call me")
        errors = response.context["form"].errors
        self.assertEqual(errors["first_name"], ["This field is required."])
        self.assertEqual(errors["email"], ["Another account already uses this email address."])
        self.assertIn("phone", errors)
        self.assertContains(response, "<h3 class=\"h2 mb-1\">Sara Ben</h3>")  # the page still shows the saved name
        self.sara.refresh_from_db()
        self.assertEqual((self.sara.first_name, self.sara.last_name, self.sara.email),
                         ("Sara", "Ben", "sara@example.com"))

    def test_an_old_shared_email_doesnt_block_other_changes(self):
        # Before Stage 17b, two accounts could have the same email (update() skips the app's rules)
        User.objects.filter(pk=self.karim.pk).update(email="sara@example.com", last_name="Sow")
        self.karim.refresh_from_db()
        self.assertEqual(messages_in(self.save_details(self.karim, first_name="Karim")), ["Changes saved."])

    def test_you_cannot_change_your_own_role(self):
        response = self.client.get(f"/administration/users/{self.amina.pk}/")
        self.assertTrue(response.context["form"].fields["role"].disabled)
        self.assertContains(response, "You can&#x27;t change your own role.")
        self.save_details(self.amina, last_name="Sy", role="user")  # sent anyway: ignored
        self.amina.refresh_from_db()
        self.assertEqual((self.amina.last_name, self.amina.role), ("Sy", "admin"))

    def test_a_superuser_is_always_an_admin(self):
        boss = User.objects.create_superuser("boss", "boss@example.com", first_name="Big", last_name="Boss")
        response = self.client.get(f"/administration/users/{boss.pk}/")
        self.assertTrue(response.context["form"].fields["role"].disabled)
        self.assertContains(response, "made with the createsuperuser command")
        self.save_details(boss, role="user")
        boss.refresh_from_db()
        self.assertEqual(boss.role, "admin")

    def test_the_page_shows_their_projects_and_open_tasks(self):
        wifi = Project.objects.create(name="Wi-Fi", manager=self.sara, status="completed")
        response = self.client.get(f"/administration/users/{self.sara.pk}/")
        self.assertEqual(response.context["managed_projects"], [wifi])
        self.assertEqual(response.context["member_projects"], [self.project])
        self.assertEqual(response.context["member_projects"][0].open_count, 1)
        self.assertEqual(response.context["open_tasks"], [self.task])
        self.assertContains(response, "Install the firewall")


# Saving a password is slow on purpose: these tests use a fast method instead (only in tests)
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NewPasswordTests(TeamTestCase):
    def setUp(self):
        self.sara.set_password("Old-pass-2026")
        self.sara.save()
        self.saras_browser = Client()
        self.saras_browser.force_login(self.sara)
        self.login(self.amina)

    def test_an_admin_gives_a_new_password(self):
        wrong_passwords("sara", ip_address="10.0.0.1")
        wrong_passwords("sara", ip_address="10.0.0.2")
        response = self.client.post(f"/administration/users/{self.sara.pk}/password/",
                                    {"new_password1": "Green-hill-2026", "new_password2": "Green-hill-2026"},
                                    follow=True)
        self.assertIn("The new password of Sara Ben is saved.", messages_in(response)[0])
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.check_password("Green-hill-2026"))
        self.assertFalse(AccessAttempt.objects.filter(username="sara").exists())  # her login is unlocked
        # Wherever she was logged in, she must log in again, with the new password
        self.assertRedirects(self.saras_browser.get("/"), "/login/?next=/")
        self.assertRedirects(Client().post("/login/", {"username": "sara", "password": "Green-hill-2026"}), "/")

    def test_a_weak_password_is_refused(self):
        response = self.client.post(f"/administration/users/{self.sara.pk}/password/",
                                    {"new_password1": "sara", "new_password2": "sara"})
        self.assertIn("This password is too short. It must contain at least 8 characters.",
                      response.context["form"].errors["new_password2"])
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.check_password("Old-pass-2026"))

    def test_your_own_password_is_changed_on_your_profile(self):
        # There, your current password is asked first
        self.assertRedirects(self.client.get(f"/administration/users/{self.amina.pk}/password/"), "/profile/password/")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DeactivateTests(TeamTestCase):
    def setUp(self):
        self.login(self.amina)

    def test_the_page_says_what_is_left_to_do(self):
        response = self.client.get(f"/administration/users/{self.omar.pk}/deactivate/")
        self.assertEqual(list(response.context["managed_projects"]), [self.project])
        response = self.client.get(f"/administration/users/{self.sara.pk}/deactivate/")
        self.assertEqual(list(response.context["open_tasks"]), [self.task])
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.is_active)  # opening the page changes nothing

    def test_deactivating_an_account(self):
        self.sara.set_password("Old-pass-2026")
        self.sara.save()
        saras_browser = Client()
        saras_browser.force_login(self.sara)

        response = self.client.post(f"/administration/users/{self.sara.pk}/deactivate/", follow=True)
        self.assertIn("The account of Sara Ben is deactivated", messages_in(response)[0])
        self.sara.refresh_from_db()
        self.assertFalse(self.sara.is_active)
        # She is logged out, and can't log in anymore
        self.assertRedirects(saras_browser.get("/"), "/login/?next=/")
        with self.assertLogs("axes", level="WARNING"):
            response = Client().post("/login/", {"username": "sara", "password": "Old-pass-2026"})
        self.assertEqual(response.status_code, 200)  # still the login page
        # Nothing is deleted: her task and her place in the team stay
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee, self.sara)
        self.assertIn(self.sara, self.project.team_queryset())

        # Reactivated: she can log in again
        self.client.post(f"/administration/users/{self.sara.pk}/reactivate/")
        self.assertRedirects(Client().post("/login/", {"username": "sara", "password": "Old-pass-2026"}), "/")

    def test_you_cannot_deactivate_yourself(self):
        response = self.client.post(f"/administration/users/{self.amina.pk}/deactivate/", follow=True)
        self.assertEqual(messages_in(response), ["You can't deactivate your own account. Another admin can."])
        self.amina.refresh_from_db()
        self.assertTrue(self.amina.is_active)
        self.assertNotContains(self.client.get(f"/administration/users/{self.amina.pk}/"), "/deactivate/")

    def test_reactivating_needs_the_button(self):
        User.objects.filter(pk=self.karim.pk).update(is_active=False)
        self.assertEqual(self.client.get(f"/administration/users/{self.karim.pk}/reactivate/").status_code, 405)
        self.assertFalse(User.objects.get(pk=self.karim.pk).is_active)

    def test_a_project_whose_manager_is_deactivated_gets_a_new_one_from_an_admin(self):
        self.client.post(f"/administration/users/{self.omar.pk}/deactivate/")
        response = self.client.get(f"/projects/{self.project.pk}/edit/")
        self.assertNotIn(self.omar, response.context["form"].fields["manager"].queryset)  # choose someone else
        self.client.post(f"/projects/{self.project.pk}/edit/", {
            "name": "Firewall", "status": "in_progress", "priority": "medium", "manager": self.sara.pk})
        self.project.refresh_from_db()
        self.assertEqual(self.project.manager, self.sara)


class ProjectsOfAPersonTests(TeamTestCase):
    def setUp(self):
        self.login(self.amina)

    def test_adding_someone_to_a_project(self):
        response = self.client.post(f"/administration/users/{self.karim.pk}/projects/add/",
                                    {"project": self.project.pk}, follow=True)
        self.assertEqual(messages_in(response), ["Karim is now in the team of Firewall."])
        membership = ProjectMember.objects.get(project=self.project, user=self.karim)
        self.assertEqual(membership.added_by, self.amina)
        self.assertIn("Amina added Karim to the team of Firewall", visible_text(self.client.get("/activity/")))
        # Karim can open the project now
        self.login(self.karim)
        self.assertEqual(self.client.get(f"/projects/{self.project.pk}/").status_code, 200)

    def test_the_list_only_offers_the_other_projects(self):
        wifi = Project.objects.create(name="Wi-Fi", manager=self.omar)
        old = Project.objects.create(name="Old laptops", manager=self.omar, status="completed")
        form = self.client.get(f"/administration/users/{self.sara.pk}/").context["add_form"]
        self.assertEqual(list(form.fields["project"].queryset), [old, wifi])  # not Firewall: she is in it
        self.assertEqual([label for label, options in form.fields["project"].choices[1:]],
                         ["Active projects", "Finished projects"])

    def test_someone_cannot_be_added_twice_or_while_deactivated(self):
        response = self.client.post(f"/administration/users/{self.sara.pk}/projects/add/",
                                    {"project": self.project.pk}, follow=True)
        self.assertIn("they are in its team already", messages_in(response)[0])
        self.assertEqual(ProjectMember.objects.filter(project=self.project, user=self.sara).count(), 1)

        User.objects.filter(pk=self.karim.pk).update(is_active=False)
        response = self.client.post(f"/administration/users/{self.karim.pk}/projects/add/",
                                    {"project": self.project.pk}, follow=True)
        self.assertIn("This account is deactivated.", messages_in(response)[0])
        self.assertFalse(ProjectMember.objects.filter(project=self.project, user=self.karim).exists())

    def test_taking_someone_out_of_a_project(self):
        self.project.add_to_team(self.karim)
        response = self.client.post(f"/administration/users/{self.karim.pk}/projects/{self.project.pk}/remove/",
                                    follow=True)
        self.assertEqual(messages_in(response), ["Karim is no longer in the team of Firewall."])
        self.assertFalse(ProjectMember.objects.filter(project=self.project, user=self.karim).exists())
        self.assertIn("Amina removed Karim from the team of Firewall", visible_text(self.client.get("/activity/")))

    def test_someone_with_open_tasks_stays_in_the_team(self):
        response = self.client.post(f"/administration/users/{self.sara.pk}/projects/{self.project.pk}/remove/",
                                    follow=True)
        self.assertEqual(messages_in(response), [
            "Sara Ben still has 1 open task in Firewall. Give it to someone else (or finish it) first."])
        self.assertTrue(ProjectMember.objects.filter(project=self.project, user=self.sara).exists())

        Task.objects.filter(pk=self.task.pk).update(status="done")  # finished tasks don't count
        self.client.post(f"/administration/users/{self.sara.pk}/projects/{self.project.pk}/remove/")
        self.assertFalse(ProjectMember.objects.filter(project=self.project, user=self.sara).exists())

    def test_the_manager_stays_in_the_team(self):
        # Even with an old member line from before he became the manager
        ProjectMember.objects.create(project=self.project, user=self.omar)
        response = self.client.post(f"/administration/users/{self.omar.pk}/projects/{self.project.pk}/remove/",
                                    follow=True)
        self.assertIn("Omar Ali manages Firewall, and the manager is always in the team.", messages_in(response)[0])
        self.assertTrue(ProjectMember.objects.filter(project=self.project, user=self.omar).exists())

    def test_someone_outside_the_team_cannot_be_taken_out(self):
        address = f"/administration/users/{self.karim.pk}/projects/{self.project.pk}/remove/"
        self.assertEqual(self.client.post(address).status_code, 404)


class ProjectAndTaskListTests(TeamTestCase):
    """Every project with its manager and team, and every task with filters."""

    def setUp(self):
        self.login(self.amina)
        # Omar also manages "Wi-Fi" (finished), whose manager before him was Karim
        self.wifi = Project.objects.create(name="Wi-Fi", manager=self.karim, status="completed")
        self.wifi.manager = self.omar
        self.wifi.save()  # Karim stays in its team, as a member
        self.survey = Task.objects.create(project=self.wifi, title="Survey", status="done", assignee=self.karim)
        self.late = Task.objects.create(project=self.project, title="Order cables", assignee=self.omar,
                                        due_date=timezone.localdate() - timedelta(days=2))
        self.nobodys = Task.objects.create(project=self.project, title="Label the cables")

    def titles(self, query=""):
        return [task.title for task in self.client.get(f"/administration/tasks/{query}").context["page"]]

    def project_names(self, query=""):
        return [project.name for project in self.client.get(f"/administration/projects/{query}").context["projects"]]

    def test_every_project_with_its_manager_and_team(self):
        ProjectMember.objects.create(project=self.project, user=self.omar)  # an old line: he is the manager now
        projects = self.client.get("/administration/projects/").context["projects"]
        self.assertEqual([project.name for project in projects], ["Firewall", "Wi-Fi"])  # finished ones too
        firewall, wifi = projects
        self.assertEqual((firewall.manager, firewall.other_members, firewall.open_count), (self.omar, [self.sara], 3))
        self.assertEqual((wifi.manager, wifi.other_members, wifi.open_count), (self.omar, [self.karim], 0))
        self.assertEqual(self.project_names("?status=active"), ["Firewall"])
        self.assertEqual(self.project_names("?status=completed"), ["Wi-Fi"])
        self.assertEqual(self.project_names("?q=wi"), ["Wi-Fi"])
        # Each project can be deleted from here; after that, the admin comes back to this list
        response = self.client.get("/administration/projects/")
        delete_page = f"/projects/{self.wifi.pk}/delete/?next=/administration/projects/"
        self.assertContains(response, delete_page)
        self.assertContains(self.client.get(delete_page), 'href="/administration/projects/" class="btn">Cancel')
        response = self.client.post(delete_page, {"name": "Wi-Fi", "next": "/administration/projects/"})
        self.assertRedirects(response, "/administration/projects/")
        self.assertFalse(Project.objects.filter(pk=self.wifi.pk).exists())

    def test_a_deactivated_manager_is_easy_to_spot(self):
        User.objects.filter(pk=self.omar.pk).update(is_active=False)
        self.assertContains(self.client.get("/administration/projects/"), "Account deactivated", count=2)

    def test_every_task_with_filters(self):
        self.assertEqual(self.titles(), ["Order cables", "Install the firewall", "Label the cables"])  # open, by date
        self.assertEqual(self.titles("?state=late"), ["Order cables"])
        self.assertEqual(self.titles("?state=done"), ["Survey"])
        self.assertEqual(len(self.titles("?state=all")), 4)
        self.assertEqual(self.titles(f"?state=all&project={self.wifi.pk}"), ["Survey"])
        self.assertEqual(self.titles(f"?person={self.sara.pk}"), ["Install the firewall"])
        self.assertEqual(self.titles("?person=none"), ["Label the cables"])
        self.assertEqual(self.titles("?q=CABLES"), ["Order cables", "Label the cables"])
        self.assertEqual(self.titles("?project=nonsense&person=nonsense&state=nonsense"), self.titles())
        response = self.client.get("/administration/tasks/?q=nothing")
        self.assertContains(response, "No task matches these filters.")

    def test_long_lists_have_pages_that_keep_the_filters(self):
        Task.objects.bulk_create(Task(project=self.project, title=f"Check port {number}") for number in range(60))
        first = self.client.get("/administration/tasks/?q=port")
        self.assertEqual((len(first.context["page"].object_list), first.context["page"].paginator.count), (50, 60))
        self.assertContains(first, 'href="?q=port&amp;page=2"')
        second = self.client.get("/administration/tasks/?q=port&page=2")
        self.assertEqual(len(second.context["page"].object_list), 10)
        self.assertContains(second, 'href="?q=port&amp;page=1"')  # back to the first page, same filters
        self.assertEqual(self.client.get("/administration/tasks/?page=999").status_code, 200)  # the last page

    def test_the_overview_counts_tasks(self):
        context = self.client.get("/administration/").context
        self.assertEqual((context["open_tasks"], context["late_tasks"]), (3, 1))


class LockedLoginTests(TeamTestCase):
    def setUp(self):
        self.login(self.amina)

    def test_the_page_shows_the_wrong_passwords_that_still_count(self):
        wrong_passwords("sara", failures=5, minutes_ago=10)  # locked
        wrong_passwords("karim", failures=2)  # not locked yet
        wrong_passwords("omar", failures=5, minutes_ago=90)  # too old: doesn't count anymore
        wrong_passwords("ghost", ip_address="41.188.10.5", failures=3)  # nobody has this username
        groups = {group.username: group for group in self.client.get("/administration/logins/").context["groups"]}
        self.assertEqual(set(groups), {"sara", "karim", "ghost"})
        self.assertTrue(groups["sara"].locked)
        self.assertFalse(groups["karim"].locked)
        self.assertEqual((groups["sara"].account, groups["ghost"].account), (self.sara, None))
        self.assertContains(self.client.get("/administration/logins/"), "No account has this username")

    def test_when_a_lock_ends(self):
        # Two web browsers: 3 wrong passwords 50 minutes ago, 2 more 10 minutes ago. Together 5: locked.
        wrong_passwords("sara", failures=3, minutes_ago=50, browser="Firefox")
        wrong_passwords("sara", failures=2, minutes_ago=10, browser="Edge")
        (group,) = failed_logins(None)
        self.assertEqual(group.failures, 5)
        # In 10 minutes the first 3 stop counting (after 1 hour), and 2 are left: unlocked
        expected = timezone.now() + timedelta(minutes=10)
        self.assertLess(abs((group.locked_until - expected).total_seconds()), 5)

    def test_unlocking_one_address_or_the_whole_username(self):
        wrong_passwords("sara", ip_address="10.0.0.1")
        wrong_passwords("sara", ip_address="10.0.0.2")
        wrong_passwords("karim")
        response = self.client.post("/administration/logins/unlock/",
                                    {"username": "sara", "ip_address": "10.0.0.1"}, follow=True)
        self.assertRedirects(response, "/administration/logins/")
        self.assertEqual(messages_in(response), ["The login with the username sara is unlocked."])
        self.assertEqual(list(AccessAttempt.objects.filter(username="sara").values_list("ip_address", flat=True)),
                         ["10.0.0.2"])
        # The button of her page unlocks her from everywhere, and comes back to her page
        page = f"/administration/users/{self.sara.pk}/"
        response = self.client.post("/administration/logins/unlock/", {"username": "sara", "next": page})
        self.assertRedirects(response, page)
        self.assertEqual(list(AccessAttempt.objects.values_list("username", flat=True)), ["karim"])  # only Karim's

    def test_strange_requests_are_refused_safely(self):
        wrong_passwords("sara")
        response = self.client.post("/administration/logins/unlock/", {"username": "sara", "ip_address": "not-an-ip"},
                                    follow=True)
        self.assertEqual(messages_in(response), ["This login couldn't be found."])
        self.assertTrue(AccessAttempt.objects.filter(username="sara").exists())
        # "next" can't send the admin to another website
        response = self.client.post("/administration/logins/unlock/",
                                    {"username": "sara", "next": "https://example.org/"})
        self.assertRedirects(response, "/administration/logins/")
        self.assertEqual(self.client.get("/administration/logins/unlock/").status_code, 405)

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_an_unlocked_person_can_log_in_again(self):
        self.sara.set_password("Right-pass-2026")
        self.sara.save()
        saras_browser = Client()
        for _attempt in range(5):
            with self.assertLogs("axes", level="WARNING"):
                saras_browser.post("/login/", {"username": "sara", "password": "wrong"})
        with self.assertLogs("axes", level="WARNING"):
            response = saras_browser.post("/login/", {"username": "sara", "password": "Right-pass-2026"})
        self.assertEqual(response.status_code, 429)  # locked

        (group,) = self.client.get("/administration/logins/").context["groups"]
        self.client.post("/administration/logins/unlock/", {"username": group.username, "ip_address": group.ip_address})
        response = saras_browser.post("/login/", {"username": "sara", "password": "Right-pass-2026"})
        self.assertRedirects(response, "/")


class DeleteProjectTests(MediaFolderMixin, TeamTestCase):
    def test_only_admins_delete_projects(self):
        address = f"/projects/{self.project.pk}/delete/"
        for person, status in [(self.omar, 403), (self.sara, 403), (self.karim, 404)]:
            self.login(person)
            self.assertEqual(self.client.get(address).status_code, status)
            self.assertEqual(self.client.post(address, {"name": "Firewall"}).status_code, status)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.login(self.omar)  # the manager edits the project, but sees no Delete button
        self.assertNotContains(self.client.get(f"/projects/{self.project.pk}/edit/"), address)

    def test_the_name_must_be_typed(self):
        self.login(self.amina)
        delete_page = f"/projects/{self.project.pk}/delete/"
        self.assertContains(self.client.get(f"/projects/{self.project.pk}/edit/"), delete_page)  # the button
        for typed in ("", "firewall", "Firewal"):
            response = self.client.post(delete_page, {"name": typed})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_after_deleting_the_admin_never_goes_to_another_website(self):
        self.login(self.amina)
        response = self.client.post(f"/projects/{self.project.pk}/delete/",
                                    {"name": "Firewall", "next": "https://example.org/"})
        self.assertRedirects(response, "/projects/")

    def test_the_page_says_what_will_be_deleted(self):
        Comment.objects.create(task=self.task, author=self.sara, body="Rack ready")
        self.login(self.amina)
        context = self.client.get(f"/projects/{self.project.pk}/delete/").context
        counts = [context[name] for name in ("task_count", "comment_count", "file_count", "team_count")]
        self.assertEqual(counts, [1, 1, 0, 2])

    def test_everything_in_the_project_goes_but_the_people_stay(self):
        Task.objects.create(project=self.project, title="Rack it", parent=self.task)
        Comment.objects.create(task=self.task, author=self.sara, body="Rack ready")
        attachment = Attachment.objects.create(task=self.task, uploaded_by=self.sara,
                                               file=SimpleUploadedFile("plan.pdf", b"%PDF-1.4"))
        storage, file_name = attachment.file.storage, attachment.file.name
        self.login(self.amina)
        with self.captureOnCommitCallbacks(execute=True):  # files are deleted once the change is final
            response = self.client.post(f"/projects/{self.project.pk}/delete/", {"name": "  Firewall "}, follow=True)
        self.assertRedirects(response, "/projects/")
        self.assertEqual(messages_in(response), ['Project "Firewall" deleted.'])
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertFalse(Task.objects.exists())
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(ProjectMember.objects.exists())
        self.assertFalse(storage.exists(file_name))
        self.assertEqual(User.objects.count(), 4)  # nobody's account is deleted
        self.assertIn("Amina deleted the project Firewall", visible_text(self.client.get("/activity/")))


class AlwaysAnAdminTests(TeamTestCase):
    """The app always keeps at least one active admin, so someone can always manage it."""

    def test_the_rule(self):
        self.assertTrue(leaves_no_active_admin(self.amina, "user", True))  # she is the only admin
        self.assertTrue(leaves_no_active_admin(self.amina, "admin", False))
        self.assertFalse(leaves_no_active_admin(self.amina, "admin", True))
        self.assertFalse(leaves_no_active_admin(self.sara, "user", False))
        User.objects.filter(pk=self.omar.pk).update(role="admin")
        self.assertFalse(leaves_no_active_admin(self.amina, "user", False))  # Omar is an admin too

    def test_the_django_admin_follows_it_too(self):
        # The person's page of Django's admin panel, sent with "Active" unticked
        data = model_to_dict(self.amina, exclude=["password", "picture", "groups", "user_permissions", "last_login"])
        data.update(is_active=False)
        form = UserAdminChangeForm(data, instance=self.amina)
        self.assertFalse(form.is_valid())
        self.assertIn("The app needs at least one active admin.", form.non_field_errors()[0])

        User.objects.filter(pk=self.omar.pk).update(role="admin")  # with another admin, it's possible
        self.assertTrue(UserAdminChangeForm(data, instance=self.amina).is_valid())
