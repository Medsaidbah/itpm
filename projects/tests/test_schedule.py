"""Behind schedule (release 1.1): the progress a project should have by today, compared with its real progress.
The rules are written at the top of projects/schedule.py."""

from datetime import date

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from projects.models import Project, Task
from projects.schedule import BEHIND_SCHEDULE_MARGIN, BY_DATES, BY_TASKS, Schedule, time_elapsed

from .base import TeamTestCase, days_from_today


class TimeElapsedTests(SimpleTestCase):
    start, end = date(2026, 1, 1), date(2026, 1, 10)  # ten days, the 1st through the 10th

    def test_before_and_on_the_first_day(self):
        self.assertEqual(time_elapsed(self.start, self.end, date(2025, 12, 20)), 0)
        self.assertEqual(time_elapsed(self.start, self.end, self.start), 0)

    def test_on_the_way(self):
        self.assertEqual(time_elapsed(self.start, self.end, date(2026, 1, 4)), 30)  # 3 days of 10 are over

    def test_the_last_day_still_has_today(self):
        self.assertEqual(time_elapsed(self.start, self.end, self.end), 90)

    def test_after_the_last_day(self):
        self.assertEqual(time_elapsed(self.start, self.end, date(2026, 1, 11)), 100)
        self.assertEqual(time_elapsed(self.start, self.end, date(2026, 3, 1)), 100)

    def test_a_project_of_one_day(self):
        day = date(2026, 1, 1)
        self.assertEqual(time_elapsed(day, day, date(2025, 12, 31)), 0)
        self.assertEqual(time_elapsed(day, day, day), 0)
        self.assertEqual(time_elapsed(day, day, date(2026, 1, 2)), 100)


class MarginTests(SimpleTestCase):
    def test_the_margin_is_ten_points(self):
        self.assertEqual(BEHIND_SCHEDULE_MARGIN, 10)

    def test_exactly_ten_points_behind_is_not_flagged_eleven_is(self):
        self.assertFalse(Schedule(expected=50, actual=40, basis=BY_TASKS).is_behind)
        self.assertTrue(Schedule(expected=51, actual=40, basis=BY_TASKS).is_behind)

    def test_ahead_of_schedule(self):
        schedule = Schedule(expected=20, actual=60, basis=BY_TASKS)
        self.assertEqual(schedule.gap, -40)
        self.assertFalse(schedule.is_behind)


class ScheduleTests(TeamTestCase):
    def make_project(self, status="in_progress", start=None, end=None, tasks=()):
        """tasks: (days from today to the due date, or None for no date; done?) for each task."""
        project = Project.objects.create(name="P", manager=self.omar, status=status, start_date=start, end_date=end)
        for number, (due_in, done) in enumerate(tasks):
            Task.objects.create(project=project, title=f"T{number}", status="done" if done else "todo",
                                due_date=None if due_in is None else days_from_today(due_in))
        return project

    def schedule(self, project):
        """The schedule computed both ways: from a project fetched alone, and from a list (with_task_counts),
        which must agree."""
        alone = Project.objects.get(pk=project.pk).schedule
        in_a_list = Project.objects.with_task_counts().get(pk=project.pk).schedule
        self.assertEqual(alone, in_a_list)
        return alone

    # ----- The worked examples of the report -----

    def test_a_all_tasks_dated(self):
        # 10 tasks: 6 were due before today, 4 are due later; 3 are done
        project = self.make_project(tasks=[(-5, True), (-4, True), (-3, True), (-3, False), (-2, False), (-1, False),
                                           (3, False), (5, False), (8, False), (12, False)])
        self.assertEqual(self.schedule(project), Schedule(expected=60, actual=30, basis=BY_TASKS))
        self.assertTrue(self.schedule(project).is_behind)

    def test_b_only_some_tasks_dated(self):
        # 5 tasks: 2 due before today, 1 due later, 2 without a date (not expected yet); 1 is done
        project = self.make_project(tasks=[(-10, True), (-2, False), (4, False), (None, False), (None, False)])
        self.assertEqual(self.schedule(project), Schedule(expected=40, actual=20, basis=BY_TASKS))
        self.assertTrue(self.schedule(project).is_behind)

    def test_c_no_task_dates_but_project_dates(self):
        # 101 days (start day through end day), 30 have passed: 30 %; 4 tasks without dates, none done
        project = self.make_project(start=days_from_today(-30), end=days_from_today(70),
                                    tasks=[(None, False)] * 4)
        self.assertEqual(self.schedule(project), Schedule(expected=30, actual=0, basis=BY_DATES))
        self.assertTrue(self.schedule(project).is_behind)

        # The same project with one task done of four: 25 %, only 5 points behind
        Task.objects.filter(pk=project.tasks.first().pk).update(status="done")
        self.assertEqual(self.schedule(project), Schedule(expected=30, actual=25, basis=BY_DATES))
        self.assertFalse(self.schedule(project).is_behind)

    def test_d_no_dates_at_all(self):
        project = self.make_project(tasks=[(None, False)] * 3)
        self.assertIsNone(self.schedule(project))

    def test_e_no_tasks(self):
        project = self.make_project(start=days_from_today(-50), end=days_from_today(50))
        self.assertIsNone(self.schedule(project))

    def test_f_completed_cancelled_and_on_hold_are_never_flagged(self):
        # Every task late and none done: would be 100 % expected, 0 % done
        for status in ("completed", "cancelled", "on_hold"):
            with self.subTest(status=status):
                project = self.make_project(status=status, start=days_from_today(-60), end=days_from_today(-10),
                                            tasks=[(-20, False), (-15, False)])
                self.assertIsNone(self.schedule(project))

    def test_g_a_planned_project_is_measured(self):
        project = self.make_project(status="planned", tasks=[(-3, False), (10, False)])
        self.assertEqual(self.schedule(project), Schedule(expected=50, actual=0, basis=BY_TASKS))

    # ----- The edges -----

    def test_a_task_due_today_still_has_today(self):
        project = self.make_project(tasks=[(0, False), (0, False)])
        self.assertEqual(self.schedule(project), Schedule(expected=0, actual=0, basis=BY_TASKS))

    def test_ten_points_behind_is_not_flagged(self):
        # 10 tasks: 4 due before today, 3 done: 40 % expected, 30 % done
        project = self.make_project(tasks=[(-3, True), (-3, True), (-3, True), (-3, False)] + [(5, False)] * 6)
        self.assertEqual(self.schedule(project).gap, 10)
        self.assertFalse(self.schedule(project).is_behind)

    def test_eleven_points_behind_is_flagged(self):
        # 101 days, 41 passed: 41 % expected; 3 of 10 tasks done: 30 %
        project = self.make_project(start=days_from_today(-41), end=days_from_today(59),
                                    tasks=[(None, True)] * 3 + [(None, False)] * 7)
        self.assertEqual(self.schedule(project).gap, 11)
        self.assertTrue(self.schedule(project).is_behind)

    def test_subtasks_count_like_in_the_progress(self):
        project = self.make_project(tasks=[(-2, False)])
        main = project.tasks.get()
        Task.objects.create(project=project, parent=main, title="Sub", due_date=days_from_today(4))
        self.assertEqual(self.schedule(project), Schedule(expected=50, actual=0, basis=BY_TASKS))

    def test_one_dated_task_is_enough_to_use_the_tasks(self):
        # The project's dates say 90 % of the time has passed, but the tasks are the plan
        project = self.make_project(start=days_from_today(-90), end=days_from_today(10),
                                    tasks=[(5, False), (None, False)])
        self.assertEqual(self.schedule(project), Schedule(expected=0, actual=0, basis=BY_TASKS))


class SchedulePageTests(TeamTestCase):
    def setUp(self):
        self.login(self.omar)

    def behind_project(self):
        # Firewall (from TeamTestCase) has one task due in 5 days; two late tasks make it 67 % expected, 0 % done
        for days in (-3, -1):
            Task.objects.create(project=self.project, title=f"Late {days}", due_date=days_from_today(days))
        return self.project

    def test_a_project_behind_schedule(self):
        response = self.client.get(self.behind_project().get_absolute_url())
        self.assertContains(response, "Behind schedule")
        self.assertContains(response, "Expected by today: 67%")
        self.assertContains(response, "Based on the tasks' due dates")

    def test_a_project_on_schedule(self):
        response = self.client.get(self.project.get_absolute_url())
        self.assertNotContains(response, "Behind schedule")
        self.assertContains(response, "Expected by today: 0%")

    def test_a_project_measured_by_its_dates(self):
        self.project.tasks.update(due_date=None)
        self.project.start_date, self.project.end_date = days_from_today(-10), days_from_today(10)
        self.project.save()
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, "Expected by today: 48%")
        self.assertContains(response, "Based on the project's start and end dates")

    def test_nothing_is_shown_for_a_project_that_is_not_measured(self):
        self.behind_project()
        self.project.status = "on_hold"
        self.project.save()
        response = self.client.get(self.project.get_absolute_url())
        self.assertNotContains(response, "Behind schedule")
        self.assertNotContains(response, 'id="schedule"')

    def test_in_french(self):
        self.omar.language = "fr"
        self.omar.save()
        response = self.client.get(self.behind_project().get_absolute_url())
        self.assertContains(response, "En retard sur le planning")
        self.assertContains(response, "Prévu à ce jour : 67 %")
        self.assertContains(response, "D'après les dates d'échéance des tâches")

    def test_in_arabic(self):
        self.omar.language = "ar"
        self.omar.save()
        response = self.client.get(self.behind_project().get_absolute_url())
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, "متأخر عن الجدول الزمني")
        self.assertContains(response, "المتوقع حتى اليوم: 67%")
        self.assertContains(response, "حسب تواريخ استحقاق المهام")

    def test_the_schedule_needs_no_extra_query_on_the_project_page(self):
        def queries():
            with CaptureQueriesContext(connection) as captured:
                self.client.get(self.project.get_absolute_url())
            return len(captured)

        on_hold = Project.objects.filter(pk=self.project.pk)
        on_hold.update(status="on_hold")  # not measured: the schedule isn't computed
        without = queries()
        on_hold.update(status="in_progress")
        self.assertEqual(queries(), without)
