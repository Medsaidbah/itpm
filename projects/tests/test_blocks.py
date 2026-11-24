"""Block records (release 1.5): each time a task is Blocked, a TaskBlock with the reason, since when and by whom,
an optional expected end, and when and by whom it ended. Opened and closed only by projects/status.py
change_status(), on every path that changes a status. The "[blocked] " comment stays as before."""

import ast
import importlib.util
import io
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import User
from projects.models import Comment, ProjectMember, Task, TaskBlock
from projects.status import change_status

from .base import TeamTestCase, days_from_today
from .test_blocking import form_data
from .test_status import source_files

LATER = date(2030, 1, 15)


class BlockTestCase(TeamTestCase):
    def blocks(self, task=None):
        return list(TaskBlock.objects.filter(task=task or self.task).order_by("started_at", "id"))

    def assert_open(self, person, reason, expected_end=None, task=None):
        blocks = self.blocks(task)
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual((block.reason, block.started_by, block.expected_end, block.ended_at),
                         (reason, person, expected_end, None))
        # The comment, exactly as before
        self.assertEqual(Comment.objects.filter(task=task or self.task).last().body, "[blocked] " + reason)

    def assert_closed(self, person, task=None):
        block = self.blocks(task)[-1]
        self.assertIsNotNone(block.ended_at)
        self.assertEqual(block.ended_by, person)


class EveryPathTests(BlockTestCase):
    """The record is opened and closed by every path that changes a status (the seven of projects/status.py)."""

    def test_1_the_task_page_buttons(self):
        self.login(self.sara)
        self.client.post(f"/tasks/{self.task.pk}/status/",
                         {"status": "blocked", "blocked_reason": "No rack", "blocked_expected_end": "2030-01-15"})
        self.assert_open(self.sara, "No rack", LATER)
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "in_progress"})
        self.assert_closed(self.sara)

    def test_2_the_board(self):
        self.login(self.sara)
        self.client.post(f"/tasks/{self.task.pk}/move/", {"status": "blocked", "order": [self.task.pk],
                                                          "blocked_reason": "No rack", "blocked_expected_end": "2030-01-15"})
        self.assert_open(self.sara, "No rack", LATER)
        self.client.post(f"/tasks/{self.task.pk}/move/", {"status": "done", "order": [self.task.pk]})
        self.assert_closed(self.sara)

    def test_3_the_new_task_form(self):
        self.login(self.omar)
        self.client.post(f"/projects/{self.project.pk}/tasks/new/", self.task_form_data(
            title="New", status="blocked", blocked_reason="No room", blocked_expected_end="2030-01-15"))
        self.assert_open(self.omar, "No room", LATER, task=Task.objects.get(title="New"))

    def test_4_the_edit_form(self):
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(
            status="blocked", blocked_reason="Budget", blocked_expected_end="2030-01-15"))
        self.assert_open(self.omar, "Budget", LATER)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(status="todo"))
        self.assert_closed(self.omar)

    def test_5_the_admin_panels_task_page(self):
        self.login(self.amina)
        url = f"/admin/projects/task/{self.task.pk}/change/"
        self.client.post(url, form_data(self.client.get(url), status=["blocked"], blocked_reason="Vendor",
                                        blocked_expected_end="2030-01-15"))
        self.assert_open(self.amina, "Vendor", LATER)
        self.client.post(url, form_data(self.client.get(url), status=["todo"]))
        self.assert_closed(self.amina)

    def test_6_the_admin_panels_revert(self):
        task = Task.objects.get(pk=self.task.pk)
        change_status(task, "in_progress", by=self.omar)
        old = task.history.latest()  # a version "in progress"
        change_status(task, "blocked", by=self.omar, reason="Waiting")
        self.login(self.amina)
        url = f"/admin/projects/task/{task.pk}/history/{old.history_id}/"
        self.client.post(url, form_data(self.client.get(url)))  # back to "in progress": the block ends
        self.assertEqual(Task.objects.get(pk=task.pk).status, "in_progress")
        self.assert_closed(self.amina)

    def test_7_the_admin_panels_tasks_table(self):
        self.login(self.amina)
        url = f"/admin/projects/project/{self.project.pk}/change/"
        self.client.post(url, form_data(self.client.get(url), **{
            "tasks-0-status": ["blocked"], "tasks-0-blocked_reason": "Electrician",
            "tasks-0-blocked_expected_end": "2030-01-15"}))
        self.assert_open(self.amina, "Electrician", LATER)
        self.client.post(url, form_data(self.client.get(url), **{"tasks-0-status": ["todo"]}))
        self.assert_closed(self.amina)


class RuleTests(BlockTestCase):
    def test_the_expected_end_is_optional(self):
        change_status(Task.objects.get(pk=self.task.pk), "blocked", by=self.sara, reason="No rack")
        self.assert_open(self.sara, "No rack", None)

    def test_an_invalid_expected_end_is_refused_and_nothing_changes(self):
        self.login(self.sara)
        response = self.client.post(f"/tasks/{self.task.pk}/status/", {
            "status": "blocked", "blocked_reason": "No rack", "blocked_expected_end": "not a date"}, follow=True)
        self.assertContains(response, "The expected end isn&#x27;t a valid date.")
        self.assertEqual((Task.objects.get(pk=self.task.pk).status, self.blocks()), ("todo", []))

    def test_a_task_blocked_before_1_5_has_no_record_to_close(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")  # blocked by an older version
        change_status(Task.objects.get(pk=self.task.pk), "in_progress", by=self.omar)
        self.assertEqual(self.blocks(), [])

    def test_staying_blocked_opens_nothing_more(self):
        task = Task.objects.get(pk=self.task.pk)
        change_status(task, "blocked", by=self.sara, reason="One")
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(status="blocked", title="Renamed"))
        self.assertEqual(len(self.blocks()), 1)

    def test_one_open_block_per_task_in_the_database_too(self):
        TaskBlock.objects.create(task=self.task, reason="A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskBlock.objects.create(task=self.task, reason="B")

    def test_each_block_has_its_own_record(self):
        task = Task.objects.get(pk=self.task.pk)
        for reason in ("First", "Second"):
            change_status(task, "blocked", by=self.sara, reason=reason)
            change_status(task, "in_progress", by=self.omar)
        self.assertEqual([b.reason for b in self.blocks()], ["First", "Second"])
        self.assertTrue(all(b.ended_by == self.omar for b in self.blocks()))


class OnlyStatusWritesBlocksTests(TeamTestCase):
    WRITES = {"create", "update", "delete", "save", "bulk_create", "bulk_update", "get_or_create", "update_or_create"}

    def test_no_other_code_writes_a_block_record(self):
        found = []
        for relative, path in source_files():  # the app's code, without tests, migrations and status.py
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "TaskBlock":
                    found.append(f"{relative}:{node.lineno} TaskBlock(...)")
                if isinstance(node.func, ast.Attribute) and node.func.attr in self.WRITES:
                    receiver = ast.unparse(node.func.value)
                    if "TaskBlock" in receiver or ".blocks" in receiver:
                        found.append(f"{relative}:{node.lineno} {receiver}.{node.func.attr}(...)")
        self.assertEqual(found, [], "Block records are written only by projects.status.change_status()")


class TaskPageTests(BlockTestCase):
    def setUp(self):
        self.lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina")
        self.eva = User.objects.create_user("eva", "eva@example.com", first_name="Eva", role=User.Role.EXECUTIVE)
        ProjectMember.objects.create(project=self.project, user=self.lina)

    def block(self, expected_end=None):
        change_status(Task.objects.get(pk=self.task.pk), "blocked", by=self.sara, reason="No rack yet",
                      expected_end=expected_end)

    def page(self, person=None):
        self.login(person or self.lina)
        return self.client.get(f"/tasks/{self.task.pk}/").content.decode()

    def test_the_open_block(self):
        self.block(days_from_today(10))
        page = self.page()
        today = timezone.localdate().strftime("%d %b %Y")
        self.assertIn(f"Blocked since {today} by Sara Ben", page)
        self.assertIn(f"Expected end: {days_from_today(10).strftime('%d %b %Y')}", page)
        self.assertNotIn("expected end passed", page)

    def test_a_passed_expected_end_is_orange(self):
        self.block(days_from_today(-1))
        page = self.page()
        self.assertIn("expected end passed", page)
        self.assertIn('class="small text-orange"', page)

    def test_earlier_blocks(self):
        self.block()
        change_status(Task.objects.get(pk=self.task.pk), "in_progress", by=self.omar)
        page = self.page()
        self.assertNotIn('id="open-block"', page)
        self.assertIn("Earlier blocks", page)
        self.assertIn("No rack yet", page)
        self.assertIn("by Sara Ben, unblocked on", page)
        self.assertIn("by Omar Ali", page)

    def test_everyone_who_sees_the_task_sees_it(self):
        self.block()
        for person in (self.lina, self.sara, self.omar, self.amina, self.eva):
            with self.subTest(person=person.username):
                self.assertIn('id="open-block"', self.page(person))
        self.login(self.karim)
        self.assertEqual(self.client.get(f"/tasks/{self.task.pk}/").status_code, 404)

    def test_a_task_blocked_before_1_5_shows_nothing_more(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")
        self.assertNotIn('id="open-block"', self.page())

    def test_the_dialog_and_the_form_offer_an_expected_end(self):
        page = self.page(self.sara)
        self.assertIn('name="blocked_expected_end"', page)
        self.login(self.omar)
        self.assertContains(self.client.get(f"/tasks/{self.task.pk}/edit/"), 'name="blocked_expected_end"')

    def test_french_and_arabic(self):
        self.block(days_from_today(-1))
        for language, texts in (("fr", ("Bloquée depuis le", "par Sara Ben", "échéance prévue dépassée")),
                                ("ar", ("متوقفة منذ", "Sara Ben", "تجاوزت النهاية المتوقعة"))):
            with self.subTest(language=language):
                User.objects.filter(pk=self.lina.pk).update(language=language)
                page = self.page()
                for text in texts:
                    self.assertIn(text, page)


class RehearsalCheckTests(TeamTestCase):
    def defaults(self):
        spec = importlib.util.spec_from_file_location(
            "rehearsal_checks", Path(settings.BASE_DIR) / "scripts" / "rehearsal_checks.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with redirect_stdout(output):
            module.defaults()
        return output.getvalue().split("\n")

    def test_the_new_table_starts_empty(self):
        self.assertIn("taskblocks-empty ok", self.defaults())
        TaskBlock.objects.create(task=self.task, reason="x")
        self.assertIn("taskblocks-empty 1-rows", self.defaults())


class MigrationTests(TransactionTestCase):
    """projects/0012 only creates the new table: a task blocked before it, and its "[blocked]" comment, stay as they
    are, and no record is made from them."""

    def test_from_0011_to_0012(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("projects", "0011_task_weight")])
        old = executor.loader.project_state([("projects", "0011_task_weight")]).apps
        person = User.objects.create_user("old-person")
        project = old.get_model("projects", "Project").objects.create(name="Old", status="in_progress", manager_id=person.pk)
        task = old.get_model("projects", "Task").objects.create(project=project, title="Old blocked", status="blocked")
        old.get_model("projects", "Comment").objects.create(task=task, author_id=person.pk, body="[blocked] Old reason")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertEqual(Task.objects.get(pk=task.pk).status, "blocked")
        self.assertEqual(Comment.objects.get(task_id=task.pk).body, "[blocked] Old reason")
        self.assertEqual(TaskBlock.objects.count(), 0)
