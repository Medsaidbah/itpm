"""Tests for people: roles, logging in and out."""

from django.test import TestCase

from .models import User


class RoleTests(TestCase):
    def test_a_superuser_is_always_an_admin(self):
        boss = User.objects.create_superuser("boss", "boss@example.com", role="member")
        self.assertEqual(boss.role, "admin")

    def test_only_admins_and_managers_can_manage(self):
        self.assertTrue(User(role="admin").can_manage)
        self.assertTrue(User(role="manager").can_manage)
        self.assertFalse(User(role="member").can_manage)

    def test_name_shown_in_the_app(self):
        self.assertEqual(str(User(username="sara", first_name="Sara", last_name="Ben")), "Sara Ben")
        self.assertEqual(str(User(username="sara")), "sara")  # no name: the username


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Made once for all the tests of this class (saving a password takes a moment)
        User.objects.create_user("sara", "sara@example.com", "a-Strong-pass-42")

    def test_log_in_and_out(self):
        response = self.client.post("/login/", {"username": "sara", "password": "a-Strong-pass-42"})
        self.assertRedirects(response, "/")
        self.assertEqual(self.client.get("/").status_code, 200)

        response = self.client.post("/logout/")  # logging out is a form (POST), not a link
        self.assertRedirects(response, "/login/")
        self.assertRedirects(self.client.get("/"), "/login/?next=/")

    def test_wrong_password(self):
        response = self.client.post("/login/", {"username": "sara", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct username and password.")

    def test_an_inactive_person_cannot_log_in(self):
        User.objects.filter(username="sara").update(is_active=False)
        response = self.client.post("/login/", {"username": "sara", "password": "a-Strong-pass-42"})
        self.assertEqual(response.status_code, 200)  # stays on the login page
        self.assertNotIn("_auth_user_id", self.client.session)
