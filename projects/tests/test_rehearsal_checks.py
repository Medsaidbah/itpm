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


class RehearsalCheckTests(TeamTestCase):
    def setUp(self):
        Comment.objects.create(task=self.task, author=self.sara, body="Secret budget figure 4242")
        self.checks = load_checks()

    def run_check(self, name):
        output = io.StringIO()
        with redirect_stdout(output):
            getattr(self.checks, name)()
        return output.getvalue()

    def test_counts_are_table_names_and_numbers_only(self):
        output = self.run_check("counts")
        for line in output.splitlines():
            self.assertRegex(line, r"^[a-z0-9_]+ \d+$")
        self.assertIn("projects_comment 1", output)

    def test_pages_are_labels_and_status_codes_only(self):
        output = self.run_check("pages")
        for line in output.splitlines():
            self.assertRegex(line, r"^(admin|user) [a-z-]+ (\d{3}|-)$")
        self.assertIn("admin administration 200", output)
        self.assertIn("user project 200", output)
        self.assertIn("user task 200", output)
        self.assertNotRegex(output, r" 5\d\d$")

    def test_nothing_from_the_rows_is_printed(self):
        output = self.run_check("counts") + self.run_check("pages")
        for text in ("Sara", "Omar", "Amina", "Firewall", "Install the firewall", "@example.com", "Secret budget", "4242"):
            self.assertNotIn(text, output)
        self.assertFalse(re.search(r"\S+@\S+", output))
