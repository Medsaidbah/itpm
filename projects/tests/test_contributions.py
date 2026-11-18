"""A person's contribution in figures (release 1.1): the definitions, who may see what, the empty cases, and the
admins' overview. The rules are written at the top of projects/contributions.py.

The dataset, around Sara (dates are counted back from now):
- Firewall, managed by Omar. Team: Sara, Lina. Sara's tasks: "Install the firewall" (open, due in 5 days, from
  TeamTestCase), done 10 days ago on time, done 40 days ago late, open and overdue. Her comments: 5 days ago and
  100 days ago. Her file: 6 days ago.
- Backup, managed by Badr. Team: Sara, Nadia. Sara's tasks: done 3 days ago without a due date, done 60 days ago
  on time, done 120 days ago, open without a date. Her comment: 2 days ago.
- Archive, cancelled, managed by Badr. Team: Sara. Her open, overdue task there doesn't count (closed project).
- Wifi, managed by Nadia, who is only a member of Backup: a manager, but of none of Sara's projects.
"""

import re
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from projects.contributions import Figures, figures_for, figures_scope
from projects.models import Attachment, Comment, Project, ProjectMember, Task

from .base import TeamTestCase, days_from_today


def ago(days):
    return timezone.now() - timedelta(days=days)


class ContributionTestCase(TeamTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina")
        cls.badr = User.objects.create_user("badr", "badr@example.com", first_name="Badr")
        cls.nadia = User.objects.create_user("nadia", "nadia@example.com", first_name="Nadia")
        ProjectMember.objects.create(project=cls.project, user=cls.lina)
        cls.backup = Project.objects.create(name="Backup", manager=cls.badr, status="in_progress")
        cls.archive = Project.objects.create(name="Archive", manager=cls.badr, status="cancelled")
        cls.wifi = Project.objects.create(name="Wifi", manager=cls.nadia, status="in_progress")
        for project, person in ((cls.backup, cls.sara), (cls.backup, cls.nadia), (cls.archive, cls.sara)):
            ProjectMember.objects.create(project=project, user=person)

        def task(project, done_days_ago=None, due_in=None):
            new = Task.objects.create(project=project, title="Task", assignee=cls.sara,
                                      status="done" if done_days_ago is not None else "todo",
                                      due_date=None if due_in is None else days_from_today(due_in))
            if done_days_ago is not None:
                Task.objects.filter(pk=new.pk).update(completed_at=ago(done_days_ago))
            return new

        task(cls.project, done_days_ago=10, due_in=-8)  # on time: done 10 days ago, due 8 days ago
        task(cls.project, done_days_ago=40, due_in=-45)  # late: done 40 days ago, due 45 days ago
        task(cls.project, due_in=-2)  # open and overdue
        task(cls.backup, done_days_ago=3)  # done, no due date: not in the on-time rate
        task(cls.backup, done_days_ago=60, due_in=-50)  # on time
        task(cls.backup, done_days_ago=120, due_in=-130)  # done too long ago for any period
        task(cls.backup)  # open, no date
        task(cls.archive, due_in=-20)  # open and overdue, but in a cancelled project

        def comment(project_task, days):
            new = Comment.objects.create(task=project_task, author=cls.sara, body="Note")
            Comment.objects.filter(pk=new.pk).update(created_at=ago(days))

        cls.backup_task = cls.backup.tasks.first()
        comment(cls.task, 5)
        comment(cls.task, 100)
        comment(cls.backup_task, 2)
        cls.file = Attachment.objects.create(task=cls.task, uploaded_by=cls.sara, file="attachments/plan.pdf")
        Attachment.objects.filter(pk=cls.file.pk).update(uploaded_at=ago(6))

    def page(self, viewer, person=None):
        self.login(viewer)
        return self.client.get(f"/people/{(person or self.sara).pk}/")


class DefinitionTests(ContributionTestCase):
    def test_all_of_saras_work(self):
        figures = figures_for([self.sara.pk])[self.sara.pk]
        newest_comment = Comment.objects.filter(task=self.backup_task).get().created_at
        self.assertEqual(figures, Figures(
            done_short=2,  # 10 and 3 days ago
            done_long=4,  # and 40 and 60 days ago (120 days is too old)
            open=3,  # "Install the firewall", the overdue one, Backup's undated one (not Archive's: cancelled)
            overdue=1,
            timed=3,  # done in 90 days with a due date: 10, 40 and 60 days ago
            on_time=2,  # 10 and 60 days ago; 40 days ago was late
            comments=2,  # 5 and 2 days ago (100 days is too old)
            files=1,
            last_activity=newest_comment,  # Backup's comment, 2 days ago
        ))
        self.assertEqual(figures.on_time_rate, 67)

    def test_only_the_work_in_the_projects_omar_manages(self):
        scope = figures_scope(self.omar, self.sara)
        figures = figures_for([self.sara.pk], scope)[self.sara.pk]
        newest_in_firewall = Comment.objects.filter(task=self.task).order_by("-created_at").first().created_at
        self.assertEqual(figures, Figures(done_short=1, done_long=2, open=2, overdue=1, timed=2, on_time=1,
                                          comments=1, files=1, last_activity=newest_in_firewall))
        self.assertEqual(figures.on_time_rate, 50)

    def test_a_task_done_on_its_due_date_is_on_time(self):
        late_by_nothing = Task.objects.create(project=self.wifi, title="Today", assignee=self.nadia, status="done",
                                              due_date=timezone.localdate())
        self.assertEqual(late_by_nothing.completed_at.date(), timezone.localdate())
        figures = figures_for([self.nadia.pk])[self.nadia.pk]
        self.assertEqual((figures.timed, figures.on_time, figures.on_time_rate), (1, 1, 100))

    def test_a_task_opened_again_is_no_longer_done(self):
        done = self.project.tasks.filter(status="done").order_by("completed_at").last()  # done 10 days ago
        done.status = "in_progress"
        done.save()  # completed_at is cleared
        figures = figures_for([self.sara.pk])[self.sara.pk]
        self.assertEqual((figures.done_short, figures.open), (1, 4))

    def test_changes_recorded_in_the_history_count_as_activity(self):
        self.login(self.lina)
        task = Task.objects.create(project=self.project, title="Lina's", assignee=self.lina)
        self.client.post(f"/tasks/{task.pk}/status/", {"status": "in_progress"})  # recorded with Lina's name
        figures = figures_for([self.lina.pk])[self.lina.pk]
        self.assertEqual(figures.last_activity, task.history.latest().history_date)


class EmptyCaseTests(ContributionTestCase):
    def test_nothing_to_measure(self):
        figures = figures_for([self.lina.pk])[self.lina.pk]
        self.assertEqual(figures, Figures())
        self.assertIsNone(figures.on_time_rate)  # not 0 %, not 100 %
        self.assertIsNone(figures.last_activity)

    def test_done_tasks_without_due_dates_give_no_rate(self):
        Task.objects.create(project=self.project, title="No date", assignee=self.lina, status="done")
        figures = figures_for([self.lina.pk])[self.lina.pk]
        self.assertEqual((figures.done_short, figures.timed, figures.on_time_rate), (1, 0, None))

    def test_the_page_shows_dashes(self):
        response = self.page(self.lina, self.lina)
        html = response.content.decode()
        on_time = html[html.index('id="on-time"'):html.index('id="last-activity"')]
        self.assertIn('<div class="datagrid-content h3 mb-0" aria-hidden="true">—</div>', on_time)
        self.assertIn("No task with a due date was done in the last 90 days", on_time)
        self.assertNotIn("%", on_time)
        self.assertContains(response, "Nothing recorded yet")


class VisibilityTests(ContributionTestCase):
    """Sara's page, opened directly by each kind of viewer."""

    def test_sara_sees_all_her_figures(self):
        response = self.page(self.sara)
        self.assertContains(response, 'id="contribution"')
        self.assertContains(response, "In all projects")
        self.assertContains(response, f'href="/activity/?person={self.sara.pk}"')
        self.assertEqual(response.context["figures"].done_long, 4)

    def test_an_admin_sees_all_of_them(self):
        response = self.page(self.amina)
        self.assertContains(response, "In all projects")
        self.assertEqual(response.context["figures"], figures_for([self.sara.pk])[self.sara.pk])

    def test_her_projects_manager_sees_only_the_work_in_his_projects(self):
        response = self.page(self.omar)
        self.assertContains(response, "Only the work in the projects you manage")
        self.assertNotContains(response, "In all projects")
        self.assertEqual(response.context["figures"].done_long, 2)  # Firewall's only
        # Activity: only in Firewall, never the general link (it would show Backup too)
        self.assertContains(response, f'href="/activity/?person={self.sara.pk}&project={self.project.pk}"')
        self.assertNotContains(response, f'href="/activity/?person={self.sara.pk}"')
        self.assertNotContains(response, f"project={self.backup.pk}")

    def test_a_co_manager_is_a_manager_too(self):
        ProjectMember.objects.filter(project=self.project, user=self.lina).update(is_manager=True)
        response = self.page(self.lina)
        self.assertContains(response, "Only the work in the projects you manage")
        self.assertEqual(response.context["figures"].done_long, 2)

    def test_a_manager_of_none_of_her_projects_sees_no_figures(self):
        response = self.page(self.nadia)  # manages Wifi; only a member of Backup with Sara
        self.assertEqual(response.status_code, 200)  # the page itself, as today
        self.assertNotContains(response, 'id="contribution"')
        self.assertIsNone(response.context["figures"])

    def test_a_teammate_sees_no_figures(self):
        response = self.page(self.lina)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="contribution"')
        self.assertNotContains(response, "Done on time")

    def test_someone_outside_her_projects_gets_nothing(self):
        self.assertEqual(self.page(self.karim).status_code, 404)

    def test_a_visitor_who_is_not_logged_in_gets_nothing(self):
        response = self.client.get(f"/people/{self.sara.pk}/")
        self.assertRedirects(response, f"/login/?next=/people/{self.sara.pk}/", fetch_redirect_response=False)

    def test_the_managers_activity_links_open_and_others_stay_closed(self):
        self.login(self.omar)
        self.assertEqual(self.client.get(f"/activity/?person={self.sara.pk}&project={self.project.pk}").status_code,
                         200)
        self.assertEqual(self.client.get(f"/activity/?person={self.sara.pk}&project={self.backup.pk}").status_code,
                         404)

    def test_a_deactivated_person_keeps_her_figures_for_admins(self):
        User.objects.filter(pk=self.sara.pk).update(is_active=False)
        response = self.page(self.amina)
        self.assertContains(response, "Account deactivated")
        self.assertEqual(response.context["figures"].done_long, 4)

    def test_the_figures_use_neutral_colours(self):
        html = self.page(self.sara).content.decode()
        card = html[html.index('id="contribution"'):html.index('<div class="row row-cards">')]
        for judging in ("text-danger", "text-red", "bg-red", "text-success", "text-green", "bg-green"):
            self.assertNotIn(judging, card)


class OverviewTests(ContributionTestCase):
    def overview(self):
        self.login(self.amina)
        return self.client.get("/administration/people/")

    def test_only_admins(self):
        for person in (self.omar, self.sara, self.lina, self.karim):
            with self.subTest(person=person.username):
                self.login(person)
                self.assertEqual(self.client.get("/administration/people/").status_code, 403)
        self.client.logout()
        response = self.client.get("/administration/people/")
        self.assertRedirects(response, "/login/?next=/administration/people/", fetch_redirect_response=False)

    def test_one_row_per_active_person_sorted_by_name_not_by_figures(self):
        User.objects.filter(pk=self.karim.pk).update(is_active=False)
        rows = self.overview().context["rows"]
        self.assertEqual([str(person) for person, _ in rows], ["Amina", "Badr", "Lina", "Nadia", "Omar Ali", "Sara Ben"])
        figures = dict(rows)
        self.assertEqual(figures[self.sara], figures_for([self.sara.pk])[self.sara.pk])
        self.assertIsNone(figures[self.lina].on_time_rate)

    def test_the_table(self):
        response = self.overview()
        html = response.content.decode()
        sara_row = html[html.index(f'href="/people/{self.sara.pk}/"'):].split("</tr>")[0]
        # Done 30 days, done 90 days, open, overdue, on time, comments, files
        self.assertEqual(re.findall(r'<td class="text-end"[^>]*>([^<]*)</td>', sara_row),
                         ["2", "4", "3", "1", "67%", "2", "1"])
        self.assertIn('title="2 of 3 tasks with a due date, in the last 90 days">67%', sara_row)
        lina_row = html[html.index(f'href="/people/{self.lina.pk}/"'):].split("</tr>")[0]
        self.assertIn('<span aria-hidden="true">—</span><span class="visually-hidden">Nothing to measure</span>', lina_row)
        for judging in ("text-danger", "text-red", "bg-red", "text-success", "text-green", "bg-green"):
            self.assertNotIn(judging, html[html.index("<table"):html.index("</table>")])

    def test_the_same_number_of_queries_however_many_people(self):
        self.login(self.amina)  # before counting: logging in again costs queries of its own

        def queries():
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get("/administration/people/").status_code, 200)
            return len(captured)

        before = queries()
        for number in range(6):
            person = User.objects.create_user(f"extra{number}", first_name=f"Extra {number}")
            Task.objects.create(project=self.project, title="More", assignee=person, status="done")
            Comment.objects.create(task=self.task, author=person, body="More")
        self.assertEqual(queries(), before)


class LanguageTests(ContributionTestCase):
    def test_in_arabic(self):
        self.sara.language = "ar"
        self.sara.save()
        response = self.page(self.sara)
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, "المساهمة")
        self.assertContains(response, "المنجزة في الموعد")
        self.assertContains(response, "2 من 3 مهام لها تاريخ استحقاق، خلال آخر 90 يومًا")

    def test_in_french(self):
        self.omar.language = "fr"
        self.omar.save()
        response = self.page(self.omar)
        self.assertContains(response, "Uniquement le travail dans les projets que vous gérez")
        self.assertContains(response, "1 sur 2 tâches avec une échéance, ces 90 derniers jours")
        self.assertContains(response, "Activité dans Firewall")

    def test_the_overview_in_arabic(self):
        self.amina.language = "ar"
        self.amina.save()
        self.login(self.amina)
        response = self.client.get("/administration/people/")
        self.assertContains(response, "نظرة عامة على الأشخاص")
        self.assertContains(response, 'dir="rtl"')
