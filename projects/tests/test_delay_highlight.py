"""Late and behind-schedule projects stand out in the project list and on the Dashboard (release 1.1).
Late (red) wins over behind schedule (orange), and the colour always comes with its word."""

import re
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

from projects.models import Project, Task

from .base import TeamTestCase, days_from_today

LATE_BADGE = '<span class="badge bg-red-lt"><i class="ti ti-alert-triangle me-1"></i>Late</span>'
BEHIND_BADGE = '<span class="badge bg-orange-lt"><i class="ti ti-clock-exclamation me-1"></i>Behind schedule</span>'


def list_row(response, project):
    """This project's row in the project list, from <tr> to </tr>."""
    html = response.content.decode()
    return re.search(rf'<tr[^>]*>\s*<td><a href="/projects/{project.pk}/">.*?</tr>', html, re.S).group(0)


def attention_item(response, project):
    """This project's line in the Dashboard's "Projects that need attention"."""
    html = response.content.decode()
    return re.search(rf'<a href="/projects/{project.pk}/" class="list-group-item.*?</a>', html, re.S).group(0)


class DelayTestCase(TeamTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Firewall (from TeamTestCase): in progress, its only task due in 5 days: on time

        # Late (its end date has passed) and also behind schedule (both tasks were due, none is done)
        cls.late = Project.objects.create(name="Late one", manager=cls.omar, status="in_progress",
                                          end_date=days_from_today(-1))
        # Behind schedule only: 30 % of its days have passed, nothing done, and no task has a date
        # (so it has no overdue task either)
        cls.behind = Project.objects.create(name="Behind one", manager=cls.omar, status="in_progress",
                                            start_date=days_from_today(-30), end_date=days_from_today(70))
        # Finished: never highlighted, whatever its dates
        cls.completed = Project.objects.create(name="Done one", manager=cls.omar, status="completed",
                                               end_date=days_from_today(-20))
        for project, due in ((cls.late, -10), (cls.late, -5), (cls.behind, None), (cls.behind, None),
                             (cls.completed, -30)):
            Task.objects.create(project=project, title="Task", due_date=due and days_from_today(due))


class DelayRuleTests(DelayTestCase):
    def delay(self, project):
        return Project.objects.with_task_counts().get(pk=project.pk).delay

    def test_late_wins_over_behind_schedule(self):
        late = Project.objects.with_task_counts().get(pk=self.late.pk)
        self.assertTrue(late.schedule.is_behind)  # it is behind schedule too...
        self.assertEqual(late.delay, "late")  # ...but shown as late only

    def test_each_kind(self):
        self.assertEqual(self.delay(self.behind), "behind")
        self.assertIsNone(self.delay(self.project))
        self.assertIsNone(self.delay(self.completed))

    def test_on_hold_is_never_behind_schedule(self):
        Project.objects.filter(pk=self.behind.pk).update(status="on_hold")
        self.assertIsNone(self.delay(self.behind))

    def test_the_same_without_the_counts_of_a_list(self):
        self.assertEqual(Project.objects.get(pk=self.late.pk).delay, "late")
        self.assertEqual(Project.objects.get(pk=self.behind.pk).delay, "behind")


class ProjectListTests(DelayTestCase):
    def setUp(self):
        self.login(self.omar)
        self.response = self.client.get("/projects/?status=all")

    def test_a_late_project_is_red_and_says_late_only(self):
        row = list_row(self.response, self.late)
        self.assertTrue(row.startswith('<tr class="project-late">'))
        self.assertIn(LATE_BADGE, row)
        self.assertNotIn("Behind schedule", row)

    def test_a_project_behind_schedule_is_orange_and_says_so(self):
        row = list_row(self.response, self.behind)
        self.assertTrue(row.startswith('<tr class="project-behind">'))
        self.assertIn(BEHIND_BADGE, row)
        self.assertNotIn(">Late<", row)

    def test_on_time_and_finished_projects_are_plain(self):
        for project in (self.project, self.completed):
            with self.subTest(project=project.name):
                row = list_row(self.response, project)
                self.assertTrue(row.startswith("<tr>"))
                self.assertNotIn("badge bg-red-lt", row)
                self.assertNotIn("badge bg-orange-lt", row)

    def test_the_coloured_edge_follows_the_direction_of_the_page(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "theme.css").read_text(encoding="utf-8")
        self.assertIn(".project-late > td:first-child { border-inline-start: 3px solid var(--tblr-red); }", css)
        self.assertIn(".project-behind > td:first-child { border-inline-start: 3px solid var(--tblr-orange); }",
                      css)
        self.assertNotIn("border-left", css)  # a left border would be on the wrong side in Arabic

    def test_the_list_costs_the_same_number_of_queries_however_many_late_projects(self):
        def queries_for_the_list():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get("/projects/?status=all").status_code, 200)
            return len(queries)

        before = queries_for_the_list()
        for number in range(6):  # three more late projects, three more behind schedule
            project = Project.objects.create(
                name=f"More {number}", manager=self.omar, status="in_progress",
                start_date=days_from_today(-50), end_date=days_from_today(-1 if number % 2 else 50))
            Task.objects.create(project=project, title="Late task", due_date=days_from_today(-3))
            Task.objects.create(project=project, title="Undated task")
        response = self.client.get("/projects/?status=all")
        self.assertEqual(response.content.decode().count('class="project-late"'), 4)
        self.assertEqual(response.content.decode().count('class="project-behind"'), 4)
        self.assertEqual(queries_for_the_list(), before)


class DashboardTests(DelayTestCase):
    def setUp(self):
        self.login(self.omar)

    def test_a_project_only_behind_schedule_now_needs_attention(self):
        item = attention_item(self.client.get("/dashboard/"), self.behind)
        self.assertIn(BEHIND_BADGE, item)
        self.assertIn("0 overdue tasks", item)
        self.assertIn("Expected by today: 30%", item)

    def test_a_late_project_says_late_only(self):
        item = attention_item(self.client.get("/dashboard/"), self.late)
        self.assertIn(LATE_BADGE, item)
        self.assertNotIn("Behind schedule", item)
        self.assertNotIn("Expected by today", item)

    def test_on_time_and_finished_projects_are_not_listed(self):
        html = self.client.get("/dashboard/").content.decode()
        self.assertNotIn(f'href="/projects/{self.project.pk}/" class="list-group-item', html)
        self.assertNotIn(f'href="/projects/{self.completed.pk}/" class="list-group-item', html)

    def test_worst_first(self):
        # Late (2 overdue tasks) before behind schedule (none). With as many overdue tasks, late comes first.
        Task.objects.create(project=self.behind, title="Late too", due_date=days_from_today(-2))
        Task.objects.create(project=self.behind, title="Late as well", due_date=days_from_today(-2))
        html = self.client.get("/dashboard/").content.decode()
        self.assertLess(html.index(f'href="/projects/{self.late.pk}/" class="list-group-item'),
                        html.index(f'href="/projects/{self.behind.pk}/" class="list-group-item'))

    def test_the_dashboard_costs_the_same_number_of_queries_however_many_projects(self):
        def queries_for_the_dashboard():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get("/dashboard/").status_code, 200)
            return len(queries)

        before = queries_for_the_dashboard()
        for number in range(4):  # each one behind schedule: 60 of 101 days passed, nothing done
            project = Project.objects.create(name=f"Behind {number}", manager=self.omar, status="in_progress",
                                             start_date=days_from_today(-60), end_date=days_from_today(40))
            Task.objects.create(project=project, title="Undated task")
        response = self.client.get("/dashboard/")
        self.assertEqual(response.content.decode().count(BEHIND_BADGE), 5)
        self.assertEqual(queries_for_the_dashboard(), before)


class LanguageTests(DelayTestCase):
    def test_in_arabic(self):
        self.omar.language = "ar"
        self.omar.save()
        self.login(self.omar)
        response = self.client.get("/projects/?status=all")
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, "tabler.rtl.min.css")  # the mirrored styles: badge spacing flips too
        self.assertIn('<i class="ti ti-alert-triangle me-1"></i>متأخرة</span>', list_row(response, self.late))
        self.assertIn('<i class="ti ti-clock-exclamation me-1"></i>متأخر عن الجدول الزمني</span>',
                      list_row(response, self.behind))
        item = attention_item(self.client.get("/dashboard/"), self.behind)
        self.assertIn("المتوقع حتى اليوم: 30%", item)

    def test_in_french(self):
        self.omar.language = "fr"
        self.omar.save()
        self.login(self.omar)
        response = self.client.get("/projects/?status=all")
        self.assertIn(">En retard</span>", list_row(response, self.late))
        self.assertIn(">En retard sur le planning</span>", list_row(response, self.behind))


class ProjectPageTests(DelayTestCase):
    def test_the_project_page_keeps_its_late_badge(self):
        self.login(self.omar)
        response = self.client.get(self.late.get_absolute_url())
        self.assertContains(response, '<span class="badge bg-red-lt">Late</span>', html=True)
