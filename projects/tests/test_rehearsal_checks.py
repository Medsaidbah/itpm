"""The checks of the migration rehearsal (scripts/rehearsal_checks.py) print counts, page labels and status codes
only: never a name, an email or other content of the rows, because they run on a copy of the live database."""

import importlib.util
import io
import re
from contextlib import redirect_stdout
from pathlib import Path

from django.conf import settings

from accounts.models import User
from projects.models import Attachment, Comment, Project, ProjectMember

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
            self.assertRegex(line, r"^(admin|user|executive|member|manager) [a-z-]+ (\d{3}( /[^\s?]*)?|-|ok|changed|"
                                   r"LEAK|missing|password-flag-cleared-in-the-copy)$")
        self.assertIn("admin administration 200", output)
        self.assertIn("user project 200", output)
        self.assertIn("user task 200", output)
        # What the rehearsal expects: 200 for every page, 403 for every change an executive tries, and nothing changed
        for line in output.splitlines():
            who, label, result = line.split(maxsplit=2)
            if label == "note":
                continue
            expected = ("403" if label.startswith("write-") else
                        "ok" if label == "nothing-changed" or label.startswith("budget-") else "200")
            self.assertEqual(result, expected, line)


class ExecutiveCheckTests(RehearsalTestCase):
    """Release 1.2: the rehearsal logs in as an executive made for the check, in the rehearsal database only."""

    def test_the_executive_reads_everything_and_changes_nothing(self):
        Attachment.objects.create(task=self.task, uploaded_by=self.sara, file="attachments/plan.pdf")
        output = self.run_check("pages")
        lines = [line for line in output.splitlines() if line.startswith("executive ")]
        reads = {line.split()[1]: line.split()[2] for line in lines
                 if "write-" not in line and "nothing" not in line and "budget-" not in line}
        writes = {line.split()[1]: line.split(maxsplit=2)[2] for line in lines if " write-" in line}
        self.assertEqual(set(reads.values()), {"200"})
        self.assertLessEqual({"project", "board", "task", "people-overview", "person-page", "dashboard"}, set(reads))
        self.assertEqual(set(writes.values()), {"403"})
        self.assertLessEqual({"write-task-status", "write-task-move", "write-comment-add", "write-file-add",
                              "write-project-edit", "write-team-add", "write-comment-delete", "write-file-delete"},
                             set(writes))
        self.assertIn("executive nothing-changed ok", output)

    def test_the_executive_is_removed_afterwards(self):
        self.run_check("pages")
        self.assertFalse(User.objects.filter(username="rehearsal-executive").exists())
        self.assertFalse(User.objects.filter(role=User.Role.EXECUTIVE).exists())


class MigrationFingerprintTests(RehearsalTestCase):
    """Release 1.3: a hash per table, over given columns, proves that no old value changed (and prints none)."""

    def prints(self):
        rows = [line.split() for line in self.run_check("fingerprint").splitlines()]
        return {table: (digest, columns) for table, digest, columns in rows}

    def test_the_same_data_gives_the_same_fingerprint(self):
        before = self.prints()
        self.assertIn("projects_project", before)
        self.assertNotIn("django_migrations", before)  # Django's bookkeeping grows with every migration
        specs = [f"{table}={columns}" for table, (_, columns) in before.items()]
        output = io.StringIO()
        with redirect_stdout(output):
            self.checks.fingerprint_of(specs)
        after = dict(line.split() for line in output.getvalue().splitlines())
        self.assertEqual(after, {table: digest for table, (digest, _) in before.items()})

    def test_one_changed_value_changes_the_fingerprint(self):
        before = self.prints()["projects_project"]
        Project.objects.filter(pk=self.project.pk).update(description="Changed")
        output = io.StringIO()
        with redirect_stdout(output):
            self.checks.fingerprint_of([f"projects_project={before[1]}"])
        self.assertNotEqual(output.getvalue().split()[1], before[0])
        self.assertNotIn("Changed", output.getvalue())

    def test_the_defaults_after_the_migration(self):
        self.assertEqual(self.run_check("defaults").strip(), "projects-defaults ok")
        Project.objects.filter(pk=self.project.pk).update(budget=10)
        self.assertEqual(self.run_check("defaults").strip(), "projects-defaults 1-rows-differ")


class BudgetCheckTests(RehearsalTestCase):
    """Release 1.3: the marker budget is hidden from an ordinary member, shown to the others, and removed afterwards."""

    def test_hidden_from_the_member_shown_to_the_others_and_put_back(self):
        lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina")
        ProjectMember.objects.create(project=self.project, user=lina)
        before = (Project.objects.filter(pk=self.project.pk).values("budget", "updated_at").get(),
                  Project.history.count())
        output = self.run_check("pages")
        budget_lines = [line for line in output.splitlines() if " budget-" in line]
        self.assertIn("member budget-hidden-project ok", budget_lines)
        self.assertIn("member budget-hidden-activity ok", budget_lines)
        self.assertIn("member budget-hidden-projects-excel ok", budget_lines)
        self.assertIn("manager budget-shown ok", budget_lines)
        self.assertIn("admin budget-shown ok", budget_lines)
        self.assertIn("executive budget-shown ok", budget_lines)
        self.assertEqual({line.split()[2] for line in budget_lines}, {"ok"})
        after = (Project.objects.filter(pk=self.project.pk).values("budget", "updated_at").get(),
                 Project.history.count())
        self.assertEqual(after, before)
        self.assertFalse(User.objects.filter(username__startswith="rehearsal-executive").exists())


class RedirectRuleTests(RehearsalTestCase):
    """Someone who still has a temporary password is sent to "change my password" on every page: the check picks
    a person who chose their own, and a redirect is shown as a path (and counted as a failure by the script)."""

    def test_a_person_with_their_own_password_is_preferred(self):
        self.sara.mark_password_as_temporary()
        self.sara.save()
        self.omar.save()  # Omar chose his own password (the default)
        output = self.run_check("pages")
        self.assertNotIn("user note", output)
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
