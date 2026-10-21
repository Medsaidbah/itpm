"""Tests of Stage 18a: logging in with the work email address, and the temporary password that
must be replaced at the first login."""

from datetime import timedelta

from axes.models import AccessAttempt
from django.test import Client, TestCase
from django.utils import timezone

from .models import User

PASSWORD = "Temp-pass-4821"


def person(username="sara", email="Sara.Ben@Example.org", temporary=False, **extra):
    user = User.objects.create_user(username, email=email, password=PASSWORD, **extra)
    if temporary:
        user.mark_password_as_temporary()
        user.save()
    return user


class LoginByEmailTests(TestCase):
    def setUp(self):
        self.sara = person()

    def login(self, name, password=PASSWORD):
        return Client().post("/login/", {"username": name, "password": password})

    def test_email_username_and_other_capitals_all_work(self):
        for name in ("sara.ben@example.org", "SARA.BEN@EXAMPLE.ORG", "sara", "Sara"):
            with self.subTest(name=name):
                response = self.login(name)
                self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_a_wrong_password_or_unknown_email_is_refused_with_one_message(self):
        for name, password in (("sara.ben@example.org", "nope"), ("nobody@example.org", PASSWORD)):
            with self.subTest(name=name):
                response = self.login(name, password)
                self.assertContains(response, "Please enter a correct work email address (or username)")

    def test_a_deactivated_account_cannot_log_in(self):
        self.sara.is_active = False
        self.sara.save()
        self.assertEqual(self.login("sara.ben@example.org").status_code, 200)

    def test_an_email_used_by_two_accounts_logs_nobody_in(self):
        person("sara2", email="sara.ben@example.org")
        self.assertEqual(self.login("sara.ben@example.org").status_code, 200)
        self.assertRedirects(self.login("sara"), "/", fetch_redirect_response=False)  # the username still works

    def test_wrong_passwords_by_email_and_by_username_count_for_the_same_account(self):
        for name in ("sara.ben@example.org", "SARA", "sara", "Sara.Ben@example.org", "sara"):
            self.login(name, "wrong")
        self.assertEqual(AccessAttempt.objects.filter(username="sara").count(), 1)
        self.assertEqual(self.login("sara").status_code, 429)  # locked after 5 failures


class TemporaryPasswordTests(TestCase):
    def setUp(self):
        self.sara = person(temporary=True)
        self.browser = Client()
        self.browser.post("/login/", {"username": "sara.ben@example.org", "password": PASSWORD})

    def test_every_page_sends_to_the_password_page(self):
        for address in ("/", "/projects/", "/profile/", "/administration/", "/admin/", "/no-such-page/"):
            with self.subTest(address=address):
                self.assertRedirects(self.browser.get(address), "/profile/password/", fetch_redirect_response=False)
        # A form can't be sent either
        response = self.browser.post("/language/", {"language": "fr", "next": "/"})
        self.assertEqual(response.status_code, 302)

    def test_the_few_allowed_pages_open(self):
        self.assertEqual(self.browser.get("/profile/password/").status_code, 200)
        self.assertContains(self.browser.get("/profile/password/"), "Choose your own password")
        self.assertEqual(self.browser.get("/healthz/").status_code, 200)
        self.assertRedirects(self.browser.post("/language/", {"language": "fr", "next": "/profile/password/"}),
                             "/profile/password/", fetch_redirect_response=False)

    def test_logging_out_is_allowed(self):
        self.assertEqual(self.browser.post("/logout/").status_code, 302)
        self.assertRedirects(self.browser.get("/"), "/login/?next=/", fetch_redirect_response=False)

    def change(self, new, old=PASSWORD):
        data = {"old_password": old, "new_password1": new, "new_password2": new}
        return self.browser.post("/profile/password/", data)

    def test_choosing_an_own_password_opens_the_app_and_keeps_the_session(self):
        response = self.change("My-own-pass-2026")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.sara.refresh_from_db()
        self.assertFalse(self.sara.must_change_password)
        self.assertIsNone(self.sara.temporary_password_until)
        self.assertTrue(self.sara.check_password("My-own-pass-2026"))
        self.assertEqual(self.browser.get("/projects/").status_code, 200)  # still logged in, no more gate

    def test_the_new_password_must_differ_from_the_temporary_one(self):
        response = self.change(PASSWORD)
        self.assertContains(response, "different from the temporary one")
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.must_change_password)

    def test_a_wrong_current_password_changes_nothing(self):
        self.change("My-own-pass-2026", old="wrong")
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.must_change_password)

    def test_a_normal_password_change_is_unchanged(self):
        free = person("omar", email="omar@example.org")
        browser = Client()
        browser.post("/login/", {"username": "omar", "password": PASSWORD})
        self.assertEqual(browser.get("/projects/").status_code, 200)
        page = browser.get("/profile/password/")
        self.assertNotContains(page, "Choose your own password")
        response = browser.post("/profile/password/", {"old_password": PASSWORD, "new_password1": "Another-2026-pw",
                                               "new_password2": "Another-2026-pw"})
        self.assertRedirects(response, "/profile/", fetch_redirect_response=False)
        free.refresh_from_db()
        self.assertFalse(free.must_change_password)

    def test_an_expired_temporary_password_is_refused(self):
        self.sara.temporary_password_until = timezone.now() - timedelta(minutes=1)
        self.sara.save()
        self.assertTrue(self.sara.temporary_password_expired)
        response = Client().post("/login/", {"username": "sara", "password": PASSWORD})
        self.assertContains(response, "Your temporary password has expired")

    def test_a_password_not_expired_yet_works(self):
        self.sara.temporary_password_until = timezone.now() + timedelta(hours=1)
        self.sara.save()
        response = Client().post("/login/", {"username": "sara", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
