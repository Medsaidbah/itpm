"""Tests of Stage 18a: creating many accounts from a list, random temporary passwords, and the
accounts an admin creates being temporary."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from accounts.models import User
from projects.tests.base import TeamTestCase

from .passwords import (
    ALPHABET,
    MAX_IMPORT_LINES,
    free_username,
    read_people,
    temporary_password,
    username_from_names,
)

LIST = "Nour;Salem;nour.salem@example.org\nYoussef;Ba;Youssef.Ba@Example.org\n"


class ReadingTests(TeamTestCase):
    def test_formats_and_header_row(self):
        for text in (LIST, LIST.replace(";", ","), LIST.replace(";", "\t"),
                     "First;Last;Email\n" + LIST, LIST.replace("\n", "\r\n") + "\n\n"):
            with self.subTest(text=text[:30]):
                result = read_people(text)
                self.assertEqual(result.errors, [])
                self.assertEqual([row[1:] for row in result.rows],
                                 [("Nour", "Salem", "nour.salem@example.org"),
                                  ("Youssef", "Ba", "Youssef.Ba@Example.org")])

    def test_every_mistake_is_reported_with_its_line(self):
        result = read_people("a;b@x.org\nNour;Salem;not-an-email\nA;B;x@example.org\nC;D;X@example.org\n"
                             "E;F;amina@example.com\n")
        lines = [line for line, _message in result.errors]
        self.assertEqual(lines, [1, 2, 4, 5])  # line 3 is fine

    def test_empty_and_too_long_lists(self):
        self.assertTrue(read_people("   \n").errors)
        many = "".join(f"A;B;p{i}@example.org\n" for i in range(MAX_IMPORT_LINES + 1))
        self.assertTrue(read_people(many).errors)

    def test_passwords_are_random_and_readable(self):
        passwords = {temporary_password() for _ in range(50)}
        self.assertEqual(len(passwords), 50)
        for password in passwords:
            self.assertRegex(password, r"^[a-z0-9]{4}(-[a-z0-9]{4}){2}$")
            self.assertTrue(set(password.replace("-", "")) <= set(ALPHABET))


class UsernameTests(TeamTestCase):
    def test_initials_of_the_first_names_and_the_last_name(self):
        for first, last, expected in [
            ("Mohamed Said", "Saidi", "mssaidi"),
            ("Sara", "Ben", "sben"),
            ("Jean-Pierre", "O'Brien", "jpobrien"),
            ("Éloïse", "Ould Ahmed", "eouldahmed"),
            ("  fatima ", "ZAHRA", "fzahra"),
        ]:
            with self.subTest(first=first, last=last):
                self.assertEqual(username_from_names(first, last), expected)

    def test_arabic_names_use_the_email(self):
        self.assertEqual(username_from_names("محمد", "سعيدي", "m.saidi@example.org"), "m.saidi")
        self.assertEqual(username_from_names("محمد", "سعيدي", ""), "user")

    def test_a_name_that_is_taken_gets_a_number(self):
        self.assertEqual(free_username("Sara", "Ben"), "sben")  # free: Sara Ben's own username is "sara"
        User.objects.create_user("sben")
        self.assertEqual(free_username("Sara", "Ben"), "sben2")
        self.assertEqual(free_username("Sara", "Ben", taken={"sben2"}), "sben3")


class ImportViewTests(TeamTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.amina)

    def test_the_accounts_are_created_with_temporary_passwords_shown_once(self):
        response = self.client.post("/administration/users/import/", {"people": LIST})
        self.assertEqual(response.status_code, 200)  # shown, not redirected: the passwords exist only here
        self.assertEqual(response["Cache-Control"], "no-store")
        usernames = User.objects.filter(email__icontains="@example.org").values_list("username", flat=True)
        self.assertEqual(sorted(usernames),
                         ["nsalem", "yba"])
        nour = User.objects.get(email="nour.salem@example.org")
        youssef = User.objects.get(email__iexact="youssef.ba@example.org")
        for account in (nour, youssef):
            self.assertEqual((account.role, account.is_active, account.must_change_password), ("user", True, True))
            self.assertIsNotNone(account.temporary_password_until)
        password = next(p for who, p in response.context["accounts"] if who == nour)
        self.assertContains(response, password)
        self.assertTrue(nour.check_password(password))
        self.assertFalse(nour.password.endswith(password))  # stored hashed
        # The person logs in with the email address and is sent to choose their own password
        browser = Client()
        browser.post("/login/", {"username": "Nour.Salem@example.org", "password": password})
        self.assertRedirects(browser.get("/projects/"), "/profile/password/", fetch_redirect_response=False)

    def test_one_mistake_creates_nobody(self):
        before = User.objects.count()
        response = self.client.post("/administration/users/import/", {"people": LIST + "Bad;Line\n"})
        self.assertContains(response, "Nobody was created")
        self.assertContains(response, "Line 3:")
        self.assertEqual(User.objects.count(), before)

    def test_a_file_can_be_sent(self):
        upload = SimpleUploadedFile("people.csv", ("﻿" + LIST).encode("utf-8"))
        response = self.client.post("/administration/users/import/", {"file": upload})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(email="nour.salem@example.org").exists())

    def test_nothing_sent(self):
        self.assertContains(self.client.post("/administration/users/import/", {}), "Paste the list")

    def test_only_admins(self):
        self.login(self.omar)
        self.assertEqual(self.client.post("/administration/users/import/", {"people": LIST}).status_code, 403)
        self.assertFalse(User.objects.filter(email="nour.salem@example.org").exists())


class AdminCreatedAccountsTests(TeamTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.amina)

    def test_two_people_with_the_same_username_in_one_list(self):
        self.client.post("/administration/users/import/",
                         {"people": "Nour;Salem;n1@example.org\nNadia;Salem;n2@example.org\nNour;Salem;n3@example.org"})
        names = User.objects.filter(email__startswith="n", email__contains="@example.org", last_name="Salem")
        self.assertEqual(sorted(names.values_list("username", flat=True)), ["nsalem", "nsalem2", "nsalem3"])

    def test_a_random_password_for_someone(self):
        response = self.client.post(f"/administration/users/{self.sara.pk}/password/", {"generate": "1"})
        self.assertEqual(response["Cache-Control"], "no-store")
        password = response.context["accounts"][0][1]
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.check_password(password))
        self.assertTrue(self.sara.must_change_password)

    def test_the_list_shows_who_has_a_temporary_password(self):
        self.client.post("/administration/users/import/", {"people": LIST})
        self.assertContains(self.client.get("/administration/users/"), "Temporary password")
