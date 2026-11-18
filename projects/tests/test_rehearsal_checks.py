"""The checks of the migration rehearsal (scripts/rehearsal_checks.py) print counts, page labels and status codes
only: never a name, an email or other content of the rows, because they run on a copy of the live database."""

import importlib.util
import io
import re
from contextlib import redirect_stdout
from pathlib import Path

from django.conf import settings

from projects.models import Comment

from .base import TeamTestCase

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "rehearsal_checks.py"


def load_checks():
    spec = importlib.util.spec_from_file_location("rehearsal_checks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # (django.setup() again is harmless)
    return module


class RehearsalTestCase(TeamTestCase):
    def setUp(self):
        Comment.objects.create(task=self.task, author=self.sara, body="Secret budget figure 4242")
        self.checks = load_checks()

    def run_check(self, name):
        output = io.StringIO()
        with redirect_stdout(output):
            getattr(self.checks, name)()
        return output.getvalue()


class RehearsalCheckTests(RehearsalTestCase):
    def test_counts_are_table_names_and_numbers_only(self):
        output = self.run_check("counts")
        for line in output.splitlines():
            self.assertRegex(line, r"^[a-z0-9_]+ \d+$")
        self.assertIn("projects_comment 1", output)

    def test_pages_are_labels_and_status_codes_only(self):
        output = self.run_check("pages")
        for line in output.splitlines():
            self.assertRegex(line, r"^(admin|user) [a-z-]+ (\d{3}( /[^\s?]*)?|-|password-flag-cleared-in-the-copy)$")
        self.assertIn("admin administration 200", output)
        self.assertIn("user project 200", output)
        self.assertIn("user task 200", output)
        # Every page of the list answers 200 (the rehearsal counts anything else as a failure)
        statuses = [line.split()[2] for line in output.splitlines() if line.split()[1] != "note"]
        self.assertEqual(set(statuses), {"200"})


class RedirectRuleTests(RehearsalTestCase):
    """Someone who still has a temporary password is sent to "change my password" on every page: the check picks
    a person who chose their own, and a redirect is shown as a path (and counted as a failure by the script)."""

    def test_a_person_with_their_own_password_is_preferred(self):
        self.sara.mark_password_as_temporary()
        self.sara.save()
        self.omar.save()  # Omar chose his own password (the default)
        output = self.run_check("pages")
        self.assertNotIn("note", output)
        self.assertIn("user home 200", output)
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.must_change_password)  # nobody's account was changed

    def test_when_nobody_chose_a_password_the_flag_is_cleared_in_the_copy(self):
        for person in (self.omar, self.sara):
            person.mark_password_as_temporary()
            person.save()
        output = self.run_check("pages")
        self.assertIn("user note password-flag-cleared-in-the-copy", output)
        self.assertIn("user home 200", output)
        self.assertNotRegex(output, r" 302")

    def test_a_redirect_is_shown_as_a_path_without_its_query_string(self):
        from django.test import Client

        self.sara.mark_password_as_temporary()
        self.sara.save()
        client = Client()
        client.force_login(self.sara)
        self.assertEqual(self.checks.describe(client.get("/projects/")), "302 /profile/password/")
        self.client.logout()
        self.assertEqual(self.checks.describe(self.client.get("/projects/?q=Firewall")), "302 /login/")
        self.assertEqual(self.checks.describe(self.client.get("/healthz/")), "200")

    def test_nothing_from_the_rows_is_printed(self):
        output = self.run_check("counts") + self.run_check("pages")
        for text in ("Sara", "Omar", "Amina", "Firewall", "Install the firewall", "@example.com", "Secret budget", "4242"):
            self.assertNotIn(text, output)
        self.assertFalse(re.search(r"\S+@\S+", output))
