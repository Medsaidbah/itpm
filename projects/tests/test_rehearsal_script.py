"""The migration rehearsal script (scripts/rehearse-migrations.ps1). Docker can't run in the tests; what can be
checked here: its error filter (through its -SelfTest switch, where PowerShell is installed) and the order of its
steps."""

import shutil
import subprocess
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "rehearse-migrations.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


class ErrorFilterTests(SimpleTestCase):
    @unittest.skipUnless(POWERSHELL, "PowerShell isn't installed here")
    def test_only_the_error_lines_are_kept_and_nothing_from_the_rows(self):
        result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                                 "-SelfTest"], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines, [
            'psycopg.OperationalError: connection failed: FATAL:  password authentication failed for user "itpm_user"',
            'django.db.utils.IntegrityError: duplicate key value violates unique constraint "accounts_user_email_key"',
            "ValueError: bad value from <email hidden> with password=<hidden> and Key (<hidden>)",
        ])
        for leak in ("example.com", "abc123", "DETAIL", 'File "', "Applying"):
            self.assertNotIn(leak, result.stdout)


class StepOrderTests(SimpleTestCase):
    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def position(self, words):
        self.assertIn(words, self.text)
        return self.text.index(words)

    def test_the_password_is_set_after_the_database_is_ready_and_before_the_restore(self):
        ready = self.position('Check "rehearsal database ready"')
        password = self.position("ALTER USER itpm_user WITH PASSWORD '$dbPassword';")
        restore = self.position("-f /tmp/rehearsal.sql")
        self.assertLess(ready, password)
        self.assertLess(password, restore)

    def test_the_schema_is_made_again_after_it_is_dropped(self):
        self.assertIn('"DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"', self.text)

    def test_failed_steps_show_their_error_lines(self):
        for step in ("Show-Errors $counting", "Show-Errors $migrate", "Show-Errors $migrateCheck",
                     "Show-Errors $systemCheck"):
            self.assertIn(step, self.text)

    def test_no_volume_is_ever_deleted(self):
        self.assertNotRegex(self.text, r"down\s+-v|--volumes|volume\s+rm|volume\s+prune")

    def test_the_script_is_plain_ascii(self):
        # Windows PowerShell 5.1 reads a file without a BOM in the old Windows code page
        data = SCRIPT.read_bytes()
        self.assertFalse([b for b in data if b > 127 or (b < 32 and b not in (9, 10, 13))])
