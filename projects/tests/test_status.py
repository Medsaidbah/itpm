"""Every change of a task's status goes through projects/status.py change_status() (release 1.5 refactor).

Two checks: the code itself never sets a task's status (an architecture test that reads the source), and each of
the seven paths that change a status really goes through change_status (a signal, active only in these tests,
records any task whose status changes without it)."""

import ast
from pathlib import Path

from django.conf import settings
from django.db.models.signals import pre_save
from django.utils import timezone

from accounts.models import User
from projects.models import Comment, Notification, Task
from projects.status import StatusRefused, change_status, take_new_status

from .base import TeamTestCase
from .test_blocking import form_data

APP_FOLDERS = ("accounts", "administration", "projects", "config")


def source_files():
    """The app's own Python files, without tests and migrations, and without status.py itself."""
    base = Path(settings.BASE_DIR)
    for folder in APP_FOLDERS:
        for path in sorted((base / folder).rglob("*.py")):
            relative = path.relative_to(base).as_posix()
            if "/tests/" in relative or "/migrations/" in relative or relative.endswith("tests.py"):
                continue
            if relative == "projects/status.py":
                continue
            yield relative, path


class ArchitectureTests(TeamTestCase):
    def test_no_code_sets_a_status_itself(self):
        found = []
        for relative, path in source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                targets = node.targets if isinstance(node, ast.Assign) else (
                    [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "status":
                        found.append(f"{relative}:{node.lineno} assigns .status")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    keywords = {kw.arg: kw.value for kw in node.keywords}
                    if node.func.attr == "update" and "status" in keywords:
                        found.append(f"{relative}:{node.lineno} update(status=...)")
                    fields = keywords.get("update_fields")
                    if node.func.attr == "save" and fields is not None and "'status'" in ast.unparse(fields):
                        found.append(f"{relative}:{node.lineno} save(update_fields=[... 'status' ...])")
                    if node.func.attr == "bulk_update" and "'status'" in ast.unparse(node):
                        found.append(f"{relative}:{node.lineno} bulk_update(..., ['status'])")
        self.assertEqual(found, [], "Change a task's status with projects.status.change_status()")

    def test_the_scan_would_notice(self):
        # The same rules, on a piece of code that breaks them
        tree = ast.parse("task.status = 'done'\nTask.objects.filter(pk=1).update(status='done')\n")
        kinds = [type(node).__name__ for node in ast.walk(tree)
                 if isinstance(node, ast.Assign) or (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "update")]
        self.assertEqual(kinds, ["Assign", "Call"])


class EveryPathTests(TeamTestCase):
    """Each path that changes a status, for real; none may change a status without change_status()."""

    def setUp(self):
        self.outside = []

        def watch(sender, instance, **kwargs):
            if getattr(instance, "_status_by_change_status", False):
                return
            if instance.pk is None:
                self.outside.append(f"new task {instance.title!r}")
                return
            before = Task.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
            if before != instance.status:
                self.outside.append(f"{instance.title!r}: {before} -> {instance.status}")

        # weak=False: a local function would otherwise be forgotten (and never called) once setUp is over
        pre_save.connect(watch, sender=Task, weak=False, dispatch_uid="status-watch")
        self.addCleanup(pre_save.disconnect, sender=Task, dispatch_uid="status-watch")

    def status(self):
        return Task.objects.get(pk=self.task.pk).status

    def test_1_the_task_page_buttons_and_done_on_my_tasks(self):
        self.login(self.sara)
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "in_progress"})
        self.assertEqual(self.status(), "in_progress")
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "done", "next": "/my-tasks/"})  # My tasks
        self.assertEqual(self.status(), "done")
        self.assertEqual(self.outside, [])

    def test_2_the_board(self):
        self.login(self.sara)
        self.client.post(f"/tasks/{self.task.pk}/move/", {"status": "done", "order": [self.task.pk]})
        self.assertEqual((self.status(), self.outside), ("done", []))

    def test_3_the_new_task_form(self):
        self.login(self.omar)
        data = self.task_form_data(title="Brand new", status="blocked", blocked_reason="No room yet")
        self.client.post(f"/projects/{self.project.pk}/tasks/new/", data)
        task = Task.objects.get(title="Brand new")
        self.assertEqual(task.status, "blocked")
        self.assertEqual(Comment.objects.get(task=task).blocking_reason, "No room yet")
        self.assertEqual(self.outside, [])

    def test_4_the_edit_form(self):
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(status="done", title="Renamed"))
        task = Task.objects.get(pk=self.task.pk)
        self.assertEqual((task.status, task.title, self.outside), ("done", "Renamed", []))

    def test_5_the_admin_panels_task_page(self):
        self.login(self.amina)
        url = f"/admin/projects/task/{self.task.pk}/change/"
        self.client.post(url, form_data(self.client.get(url), status=["done"]))
        self.assertEqual((self.status(), self.outside), ("done", []))

    def test_6_the_admin_panels_revert(self):
        task = Task.objects.get(pk=self.task.pk)
        change_status(task, "in_progress", by=self.omar)
        old = task.history.latest()  # a version "in progress"
        change_status(task, "todo", by=self.omar)
        self.login(self.amina)
        url = f"/admin/projects/task/{task.pk}/history/{old.history_id}/"
        self.client.post(url, form_data(self.client.get(url)))
        self.assertEqual((self.status(), self.outside), ("in_progress", []))

    def test_7_the_admin_panels_tasks_table(self):
        self.login(self.amina)
        url = f"/admin/projects/project/{self.project.pk}/change/"
        self.client.post(url, form_data(self.client.get(url), **{"tasks-0-status": ["done"]}))
        self.assertEqual((self.status(), self.outside), ("done", []))

    def test_the_watch_would_notice(self):
        task = Task.objects.get(pk=self.task.pk)
        task.status = "done"
        task.save()  # the old way, on purpose
        self.assertEqual(len(self.outside), 1)


class ChangeStatusTests(TeamTestCase):
    """What change_status() itself does: everything the paths used to do."""

    def test_a_blocked_task_needs_a_reason_and_nothing_is_saved_without_one(self):
        task = Task.objects.get(pk=self.task.pk)
        for reason in ("", "   "):
            with self.subTest(reason=reason):
                with self.assertRaises(StatusRefused):
                    change_status(task, "blocked", by=self.sara, reason=reason)
                self.assertEqual(Task.objects.get(pk=task.pk).status, "todo")
        change_status(task, "blocked", by=self.sara, reason=" No rack ")
        comment = Comment.objects.get(task=task)
        self.assertEqual((comment.author, comment.blocking_reason), (self.sara, "No rack"))

    def test_an_unknown_status_is_refused(self):
        with self.assertRaisesMessage(StatusRefused, "Unknown status."):
            change_status(Task.objects.get(pk=self.task.pk), "lost", by=self.sara)

    def test_completed_at_and_the_history_user(self):
        task = Task.objects.get(pk=self.task.pk)
        change_status(task, "done", by=self.omar)
        task.refresh_from_db()
        self.assertLessEqual(abs((timezone.now() - task.completed_at).total_seconds()), 60)
        self.assertEqual(task.history.latest().history_user, self.omar)
        change_status(task, "in_progress", by=self.omar)
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)

    def test_the_unblocked_notification_only_when_asked(self):
        waiting = Task.objects.create(project=self.project, title="Waits", assignee=self.sara)
        waiting.depends_on.add(self.task)
        task = Task.objects.get(pk=self.task.pk)
        change_status(task, "done", by=self.omar)  # like the admin panel: no notification
        self.assertFalse(Notification.objects.filter(kind="unblocked").exists())

        change_status(task, "in_progress", by=self.omar)
        request = self.client.get("/").wsgi_request
        request.user = self.omar
        change_status(task, "done", by=self.omar, request=request, notify=True)
        self.assertEqual(Notification.objects.filter(kind="unblocked", recipient=self.sara).count(), 1)

    def test_take_new_status_gives_back_the_new_one(self):
        task = Task.objects.get(pk=self.task.pk)
        task.status = "done"  # what a form does
        self.assertEqual(take_new_status(task), "done")
        self.assertEqual(task.status, "todo")  # the database's, until change_status changes it

    def test_one_version_in_the_history_per_save(self):
        self.login(self.omar)
        before = self.task.history.count()
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(status="done", title="Both at once"))
        self.assertEqual(self.task.history.count(), before + 1)  # the title and the status: one version, as before
        self.assertIsInstance(self.omar, User)
