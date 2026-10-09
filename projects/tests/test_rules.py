"""The rules that keep the data correct: dates, subtasks, "waits for", done dates, late tasks."""

from django.core.exceptions import ValidationError

from projects.forms import TaskForm
from projects.models import Project, Task

from .base import TeamTestCase, days_from_today


class DateRuleTests(TeamTestCase):
    def test_due_date_cannot_be_before_start_date(self):
        task = Task(project=self.project, title="Backwards",
                    start_date=days_from_today(5), due_date=days_from_today(1))
        with self.assertRaises(ValidationError) as error:
            task.full_clean()  # runs all the checks, like a form does
        self.assertIn("The due date can't be before the start date.", error.exception.message_dict["due_date"])

    def test_the_form_page_shows_the_error_and_saves_nothing(self):
        self.login(self.omar)
        response = self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(
            start_date=days_from_today(5), due_date=days_from_today(1)))
        self.assertEqual(response.status_code, 200)  # the form is shown again...
        self.assertContains(response, "The due date can&#x27;t be before the start date.")  # ...with the error
        self.task.refresh_from_db()
        self.assertIsNone(self.task.start_date)  # ...and nothing was saved

    def test_project_end_date_cannot_be_before_start_date(self):
        project = Project(name="Backwards", manager=self.omar,
                          start_date=days_from_today(5), end_date=days_from_today(1))
        with self.assertRaises(ValidationError) as error:
            project.full_clean()
        self.assertIn("end_date", error.exception.message_dict)


class SubtaskRuleTests(TeamTestCase):
    def setUp(self):
        self.subtask = Task.objects.create(project=self.project, parent=self.task, title="Rack it")

    def test_a_subtask_cannot_have_subtasks(self):
        task = Task(project=self.project, parent=self.subtask, title="Too deep")
        with self.assertRaises(ValidationError) as error:
            task.full_clean()
        self.assertIn("A subtask can't have its own subtasks.", error.exception.message_dict["parent"])

    def test_a_subtask_stays_in_its_main_task_project(self):
        other_project = Project.objects.create(name="Wi-Fi", manager=self.omar)
        task = Task(project=other_project, parent=self.task, title="Elsewhere")
        with self.assertRaises(ValidationError):
            task.full_clean()

    def test_a_main_task_with_subtasks_cannot_become_a_subtask(self):
        other = Task.objects.create(project=self.project, title="Other main task")
        self.task.parent = other
        with self.assertRaises(ValidationError) as error:
            self.task.full_clean()
        self.assertIn("This task has its own subtasks, so it can't become a subtask.",
                      error.exception.message_dict["parent"])

    def test_deleting_a_main_task_deletes_its_subtasks(self):
        self.task.delete()
        self.assertFalse(Task.objects.filter(pk=self.subtask.pk).exists())


class WaitsForRuleTests(TeamTestCase):
    def test_loops_are_refused(self):
        order = Task.objects.create(project=self.project, title="Order the hardware")
        self.task.depends_on.add(order)  # "Install" waits for "Order"
        # Now try to make "Order" wait for "Install": that would be a loop
        form = TaskForm(self.task_form_data(order, depends_on=[self.task.pk]),
                        instance=order, project=self.project)
        self.assertFalse(form.is_valid())
        self.assertIn('"Install the firewall" already waits for this task, so this task can\'t wait for it.',
                      form.errors["depends_on"])

    def test_waiting_for_lists_only_unfinished_tasks(self):
        order = Task.objects.create(project=self.project, title="Order", status="done")
        test = Task.objects.create(project=self.project, title="Test")
        self.task.depends_on.add(order, test)
        self.assertEqual(self.task.waiting_for, [test])


class DoneAndLateTests(TeamTestCase):
    def test_the_done_date_is_set_and_cleared(self):
        self.task.status = "done"
        self.task.save()
        self.assertIsNotNone(self.task.completed_at)
        self.task.status = "in_progress"  # opened again
        self.task.save()
        self.assertIsNone(self.task.completed_at)

    def test_late_tasks(self):
        late = Task(project=self.project, title="Late", due_date=days_from_today(-1))
        due_today = Task(project=self.project, title="Today", due_date=days_from_today(0))
        finished = Task(project=self.project, title="Finished", due_date=days_from_today(-1), status="done")
        no_date = Task(project=self.project, title="No date")
        self.assertTrue(late.is_overdue)
        self.assertFalse(due_today.is_overdue)  # due today is not late yet
        self.assertFalse(finished.is_overdue)
        self.assertFalse(no_date.is_overdue)

    def test_late_projects(self):
        self.project.end_date = days_from_today(-1)
        self.assertTrue(self.project.is_overdue)
        self.project.status = "completed"  # finished projects are never late
        self.assertFalse(self.project.is_overdue)

    def test_progress(self):
        for i in range(3):
            Task.objects.create(project=self.project, title=f"Task {i}", status="done" if i == 0 else "todo")
        # 4 tasks in the project, 1 done
        self.assertEqual(self.project.progress, 25)
        self.assertEqual(Project.objects.with_task_counts().get(pk=self.project.pk).progress, 25)
