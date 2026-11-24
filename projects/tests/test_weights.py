"""Task weights (release 1.4): 1, 2, 3 or 5, default 1. A project's progress and expected progress are weighted:
the weights of the done (or due) tasks over the weights of all its tasks. With every weight at 1, every figure is
exactly what it was before. Project editors set the weight; everyone who sees the task sees it."""

import importlib.util
import io
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from openpyxl import load_workbook

from accounts.models import User
from projects.models import Project, ProjectMember, Task

from .base import TeamTestCase, days_from_today


def load_rehearsal_checks():
    spec = importlib.util.spec_from_file_location(
        "rehearsal_checks", Path(settings.BASE_DIR) / "scripts" / "rehearsal_checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WeightTestCase(TeamTestCase):
    def make_project(self, tasks, status="in_progress", start=None, end=None):
        """tasks: (weight, done?, days from today to the due date or None) for each task."""
        project = Project.objects.create(name="Weighted", manager=self.omar, status=status,
                                         start_date=start and days_from_today(start), end_date=end and days_from_today(end))
        for number, (weight, done, due_in) in enumerate(tasks):
            Task.objects.create(project=project, title=f"T{number}", weight=weight, status="done" if done else "todo",
                                due_date=None if due_in is None else days_from_today(due_in))
        return project

    def figures(self, project):
        """(progress, expected) both ways: the project alone, and in a list (with_task_counts), which must agree."""
        alone = Project.objects.get(pk=project.pk)
        listed = Project.objects.with_task_counts().get(pk=project.pk)
        self.assertEqual(alone.progress, listed.progress)
        self.assertEqual(alone.schedule, listed.schedule)
        return alone.progress, alone.schedule.expected if alone.schedule else None


class WeightedProgressTests(WeightTestCase):
    def test_the_weights_of_the_done_tasks_over_all_the_weights(self):
        # 5 done, 3 + 1 + 1 not done: 5 / 10 = 50 %, where counting tasks would say 1 / 4 = 25 %
        project = self.make_project([(5, True, None), (3, False, None), (1, False, None), (1, False, None)])
        self.assertEqual(self.figures(project), (50, None))

    def test_the_expected_progress_is_weighted_too(self):
        # Due before today: 3 and 2 (5 of 11); later: 5 and 1. Done: the 2. Expected 45 %, done 18 %.
        project = self.make_project([(3, False, -4), (2, True, -1), (5, False, 6), (1, False, None)])
        self.assertEqual(self.figures(project), (18, 45))
        self.assertTrue(Project.objects.with_task_counts().get(pk=project.pk).schedule.is_behind)

    def test_a_task_due_today_still_has_today(self):
        project = self.make_project([(5, False, 0), (1, False, -2)])
        self.assertEqual(self.figures(project), (0, 17))  # only the 1 is due: 1 / 6

    def test_subtasks_count_with_their_own_weight(self):
        project = self.make_project([(2, False, None)])
        main = project.tasks.get()
        Task.objects.create(project=project, parent=main, title="Sub", weight=3, status="done")
        self.assertEqual(self.figures(project)[0], 60)  # 3 / 5, the main task and its subtask both counting

    def test_no_tasks_and_no_dates(self):
        self.assertEqual(self.figures(self.make_project([])), (0, None))
        self.assertEqual(self.figures(self.make_project([(3, True, None)])), (100, None))

    def test_the_time_based_expectation_is_unchanged(self):
        project = self.make_project([(5, False, None), (1, True, None)], start=-30, end=70)
        self.assertEqual(self.figures(project), (17, 30))  # 30 of 101 days, whatever the weights


class SameAsBeforeTests(WeightTestCase):
    """With every weight at 1 (all existing tasks after the migration), every figure is the release 1.3 one."""

    def old_figures(self, project):
        """Release 1.3's formulas: counting tasks."""
        tasks = list(project.tasks.all())
        done = sum(1 for t in tasks if t.status == "done")
        progress = round(done * 100 / len(tasks)) if tasks else 0
        expected = None
        if project.status in ("planned", "in_progress") and tasks and any(t.due_date for t in tasks):
            due = sum(1 for t in tasks if t.due_date and t.due_date < days_from_today(0))
            expected = round(due * 100 / len(tasks))
        return progress, expected

    def test_every_project_keeps_its_figures(self):
        shapes = [
            [(1, True, -5), (1, False, -3), (1, False, 4)],
            [(1, True, None), (1, True, None), (1, False, None)],
            [(1, False, -1), (1, False, -2), (1, True, -3), (1, False, 9), (1, False, None), (1, True, 2)],
            [(1, True, 1)],
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                project = self.make_project(shape)
                self.assertEqual(self.figures(project), self.old_figures(project))

    def test_the_rehearsal_works_them_out_like_the_app(self):
        projects = [self.make_project([(5, True, -3), (2, False, 4), (1, False, None)]),
                    self.make_project([(3, False, None), (1, True, None)], start=-10, end=10),
                    self.make_project([(2, False, -1)], status="on_hold"),
                    self.make_project([])]
        figures = load_rehearsal_checks().progress_figures()
        for project in projects + [self.project]:
            with self.subTest(project=project.pk):
                self.assertEqual(figures[project.pk], self.figures(project))


class WhoSetsTheWeightTests(WeightTestCase):
    def setUp(self):
        self.lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina")
        self.nadia = User.objects.create_user("nadia", "nadia@example.com", first_name="Nadia")
        self.eva = User.objects.create_user("eva", "eva@example.com", first_name="Eva", role=User.Role.EXECUTIVE)
        ProjectMember.objects.create(project=self.project, user=self.lina)
        ProjectMember.objects.create(project=self.project, user=self.nadia, is_manager=True)

    def test_each_role(self):
        # (person, what the edit form answers, whether the weight changes)
        roles = [(self.amina, 302, True), (self.omar, 302, True), (self.nadia, 302, True),
                 (self.sara, 403, False), (self.lina, 403, False), (self.eva, 403, False), (self.karim, 404, False)]
        for person, status, changes in roles:
            with self.subTest(person=person.username):
                Task.objects.filter(pk=self.task.pk).update(weight=1)
                self.login(person)
                response = self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(weight=5))
                self.assertEqual(response.status_code, status)
                self.assertEqual(Task.objects.get(pk=self.task.pk).weight, 5 if changes else 1)

    def test_only_1_2_3_or_5(self):
        self.login(self.omar)
        for weight in (0, 4, 6, -1):
            with self.subTest(weight=weight):
                response = self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(weight=weight))
                self.assertEqual(response.status_code, 200)
                self.assertIn("weight", response.context["form"].errors)
        self.assertEqual(Task.objects.get(pk=self.task.pk).weight, 1)

    def test_a_form_opened_before_the_update_keeps_the_weight(self):
        Task.objects.filter(pk=self.task.pk).update(weight=3)
        data = self.task_form_data()
        data.pop("weight", None)
        self.login(self.omar)
        self.assertEqual(self.client.post(f"/tasks/{self.task.pk}/edit/", data).status_code, 302)
        self.assertEqual(Task.objects.get(pk=self.task.pk).weight, 3)

    def test_everyone_who_sees_the_task_sees_the_weight(self):
        Task.objects.filter(pk=self.task.pk).update(weight=5)
        for person in (self.lina, self.sara, self.eva):
            with self.subTest(person=person.username):
                self.login(person)
                self.assertContains(self.client.get(f"/tasks/{self.task.pk}/"), "Very large (5)")

    def test_the_task_export(self):
        Task.objects.filter(pk=self.task.pk).update(weight=3)
        self.login(self.lina)
        sheet = load_workbook(io.BytesIO(self.client.get(f"/projects/{self.project.pk}/export.xlsx").content))["Tasks"]
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(sheet.cell(row=2, column=headers.index("Weight") + 1).value, 3)

    def test_a_weight_change_appears_in_the_activity(self):
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(weight=5))
        self.login(self.lina)
        response = self.client.get(f"/activity/?task={self.task.pk}")
        self.assertContains(response, "Weight")
        self.assertContains(response, "Very large (5)")


class LanguageTests(WeightTestCase):
    def test_french_and_arabic(self):
        Task.objects.filter(pk=self.task.pk).update(weight=2)
        for language, label, value in (("fr", "Poids", "Moyen (2)"), ("ar", "الوزن", "متوسط (2)")):
            with self.subTest(language=language):
                User.objects.filter(pk=self.omar.pk).update(language=language)
                self.login(self.omar)
                page = self.client.get(f"/tasks/{self.task.pk}/")
                self.assertContains(page, label)
                self.assertContains(page, value)
                self.assertContains(self.client.get(f"/tasks/{self.task.pk}/edit/"), value)


class OlderVersionTests(WeightTestCase):
    """If MTNIMA goes back to 1.3.0 after this migration, that version writes tasks without the weight: the database
    gives them 1 itself (db_default), so creating tasks still works there."""

    def test_a_task_written_without_the_weight(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects_task (project_id, title, description, status, priority, is_milestone, position,"
                " created_at, updated_at) VALUES (%s, 'Written by 1.3.0', '', 'todo', 'medium', false, 0, now(), now())"
                " RETURNING id", [self.project.pk])
            pk = cursor.fetchone()[0]
        self.assertEqual(Task.objects.get(pk=pk).weight, 1)


class MigrationTests(TransactionTestCase):
    """projects/0011 only adds the weight: from 0010, every task and history row keeps its data, gets the weight 1,
    and every project keeps its progress."""

    def test_from_0010_to_0011(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("projects", "0010_project_type_budget")])
        old = executor.loader.project_state([("projects", "0010_project_type_budget")]).apps
        manager = User.objects.create_user("old-manager")
        project = old.get_model("projects", "Project").objects.create(name="Old", status="in_progress", manager_id=manager.pk)
        old_task = old.get_model("projects", "Task")
        for number, status in enumerate(("done", "done", "todo")):
            old_task.objects.create(project=project, title=f"Old {number}", status=status, priority="high")
        task = old_task.objects.first()
        old.get_model("projects", "HistoricalTask").objects.create(
            id=task.pk, project_id=project.pk, title=task.title, status="todo", priority="high", is_milestone=False,
            position=0, created_at=task.created_at, updated_at=task.updated_at, history_date=task.created_at,
            history_type="+")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        tasks = Task.objects.filter(project_id=project.pk).order_by("pk")
        self.assertEqual([(t.title, t.status, t.priority, t.weight) for t in tasks],
                         [("Old 0", "done", "high", 1), ("Old 1", "done", "high", 1), ("Old 2", "todo", "high", 1)])
        self.assertEqual(Project.objects.get(pk=project.pk).progress, 67)  # 2 of 3, as before
        self.assertEqual(Task.history.get(id=task.pk).weight, 1)
