"""Tests for people: roles, logging in and out, and the lock after too many wrong passwords."""

import importlib
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase, override_settings

from projects.models import Project

from .models import User

# The migration of Stage 16c. Its name starts with a number, so it's loaded this way.
roles_migration = importlib.import_module("accounts.migrations.0002_admin_and_user_roles")


class RoleTests(TestCase):
    def test_a_superuser_is_always_an_admin(self):
        boss = User.objects.create_superuser("boss", "boss@example.com", role="user")
        self.assertEqual(boss.role, "admin")

    def test_two_roles_admin_and_user(self):
        self.assertEqual(User.Role.values, ["admin", "user"])
        self.assertEqual(User().role, "user")  # new people are users
        self.assertTrue(User(role="admin").is_admin)
        self.assertFalse(User(role="user").is_admin)

    def test_the_old_manager_and_member_roles_become_user(self):
        # Like the data before Stage 16c
        for username, role in [("boss", "admin"), ("omar", "manager"), ("sara", "member")]:
            User.objects.create_user(username, role=role)
        Project.objects.create(name="Firewall", manager=User.objects.get(username="omar"))

        roles_migration.to_admin_and_user(apps, None)
        roles = dict(User.objects.values_list("username", "role"))
        self.assertEqual(roles, {"boss": "admin", "omar": "user", "sara": "user"})

        roles_migration.back_to_manager_and_member(apps, None)  # going back
        roles = dict(User.objects.values_list("username", "role"))
        self.assertEqual(roles, {"boss": "admin", "omar": "manager", "sara": "member"})

    def test_name_shown_in_the_app(self):
        self.assertEqual(str(User(username="sara", first_name="Sara", last_name="Ben")), "Sara Ben")
        self.assertEqual(str(User(username="sara")), "sara")  # no name: the username


class AdminPanelTests(TestCase):
    def test_only_admins_open_the_admin_panel(self):
        boss = User.objects.create_superuser("boss", "boss@example.com")
        # A user given "Staff status" by mistake, without the Admin role
        omar = User.objects.create_user("omar", "omar@example.com", is_staff=True)
        self.client.force_login(omar)
        self.assertRedirects(self.client.get("/admin/"), "/admin/login/?next=/admin/")
        self.assertNotContains(self.client.get("/"), "Django admin (technical)")
        self.client.force_login(boss)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertContains(self.client.get("/"), "Django admin (technical)")


# Checking a password is slow on purpose (to slow down password guessing). These tests check
# many passwords, so they use a fast method instead: only here, never in the real app.
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Made once for all the tests of this class (saving a password takes a moment)
        User.objects.create_user("sara", "sara@example.com", "a-Strong-pass-42")

    def log_in(self, password, username="sara"):
        return self.client.post("/login/", {"username": username, "password": password})

    def wrong_password(self, username="sara"):
        # Each failure is written in the log: assertLogs catches it, so the test output stays clean
        with self.assertLogs("axes", level="WARNING"):
            return self.log_in("wrong", username)

    def test_log_in_and_out(self):
        self.assertRedirects(self.log_in("a-Strong-pass-42"), "/")
        self.assertEqual(self.client.get("/").status_code, 200)

        response = self.client.post("/logout/")  # logging out is a form (POST), not a link
        self.assertRedirects(response, "/login/")
        self.assertRedirects(self.client.get("/"), "/login/?next=/")

    def test_wrong_password(self):
        response = self.wrong_password()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct work email address (or username) and password.")

    def test_an_inactive_person_cannot_log_in(self):
        User.objects.filter(username="sara").update(is_active=False)
        with self.assertLogs("axes", level="WARNING"):
            response = self.log_in("a-Strong-pass-42")
        self.assertEqual(response.status_code, 200)  # stays on the login page
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_too_many_wrong_passwords_lock_the_login(self):
        for attempt in range(5):
            self.wrong_password()
        # Now even the right password is refused, with the "locked" page
        with self.assertLogs("axes", level="WARNING"):
            response = self.log_in("a-Strong-pass-42")
        self.assertEqual(response.status_code, 429)  # 429 = "too many requests"
        self.assertContains(response, "Too many wrong passwords", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

        # An admin can unlock it (the Admin panel can too: Axes > Access attempts)
        call_command("axes_reset_username", "sara", stdout=StringIO())
        self.assertRedirects(self.log_in("a-Strong-pass-42"), "/")

    def test_a_correct_password_forgets_earlier_mistakes(self):
        for attempt in range(4):
            self.wrong_password()
        self.assertRedirects(self.log_in("a-Strong-pass-42"), "/")  # the counter starts again
        self.client.post("/logout/")
        for attempt in range(4):
            self.wrong_password()
        self.assertRedirects(self.log_in("a-Strong-pass-42"), "/")  # still not locked
