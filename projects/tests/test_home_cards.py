"""The home page's "Projects at a glance" cards (release 1.1), the project list filters they link to, and the
Dashboard's "Behind schedule" card. Each card's number must be the number of rows of the list it opens."""

import re
from io import BytesIO

from django.db import connection
from django.test.utils import CaptureQueriesContext
from openpyxl import load_workbook

from projects.models import Project, ProjectMember, Task

from .base import TeamTestCase, days_from_today

CARDS = ["planned", "in_progress", "on_hold", "completed", "late", "behind", "blocked"]


def card_numbers(response):
    """{"planned": 1, ...}: the number shown on each card of the home page."""
    html = response.content.decode()
    numbers = {}
    for key in CARDS:
        card = re.search(rf'id="card-{key}">.*?</a>', html, re.S).group(0)
        numbers[key] = int(re.search(r'<div class="h1 mb-0[^"]*">(\d+)</div>', card).group(1))
    return numbers


def card_link(response, key):
    return re.search(rf'href="([^"]+)" class="card card-sm card-link h-100" id="card-{key}"',
                     response.content.decode()).group(1)


def list_rows(response):
    """The projects (their numbers) in the rows of the project list."""
    return [int(pk) for pk in re.findall(r'<tr[^>]*>\s*<td><a href="/projects/(\d+)/">', response.content.decode())]


class CardsTestCase(TeamTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Firewall (from TeamTestCase): in progress, on time. Omar manages it; Sara is in its team.

        def project(name, status, manager=None, start=None, end=None, tasks=()):
            new = Project.objects.create(name=name, manager=manager or cls.omar, status=status,
                                         start_date=start and days_from_today(start),
                                         end_date=end and days_from_today(end))
            for due, task_status in tasks:
                Task.objects.create(project=new, title="Task", status=task_status,
                                    due_date=due and days_from_today(due))
            return new

        cls.planned_late = project("Planned late", "planned", end=-1)
        cls.behind = project("Behind", "in_progress", start=-30, end=70, tasks=[(None, "todo"), (None, "todo")])
        cls.blocked = project("Blocked", "on_hold", tasks=[(None, "blocked"), (None, "done")])
        # Late and behind schedule at once: counted as late only
        cls.late_and_behind = project("Late and behind", "in_progress", end=-2, tasks=[(-10, "todo"), (-5, "todo")])
        # Finished: in its status card only, whatever its dates and tasks
        cls.completed = project("Completed", "completed", end=-20, tasks=[(-30, "blocked")])
        # Cancelled: in no card at all
        cls.cancelled = project("Cancelled", "cancelled", end=-20, tasks=[(-30, "blocked")])
        # Karim's own project: only Karim and the admin see it
        cls.karims = project("Karim's", "in_progress", manager=cls.karim, end=-3, tasks=[(None, "blocked")])
        ProjectMember.objects.create(project=cls.blocked, user=cls.sara)


class CardNumberTests(CardsTestCase):
    def test_the_numbers_for_the_manager_of_most_projects(self):
        self.login(self.omar)
        self.assertEqual(card_numbers(self.client.get("/")), {
            "planned": 1, "in_progress": 3, "on_hold": 1, "completed": 1,  # Firewall, Behind, Late and behind
            "late": 2,  # Planned late, Late and behind
            "behind": 1,  # Behind only: "Late and behind" counts as late
            "blocked": 1,  # Blocked (the completed and cancelled projects' blocked tasks don't count)
        })

    def test_the_numbers_only_count_the_projects_the_person_can_see(self):
        self.login(self.sara)  # in the teams of Firewall and Blocked
        self.assertEqual(card_numbers(self.client.get("/")), {
            "planned": 0, "in_progress": 1, "on_hold": 1, "completed": 0, "late": 0, "behind": 0, "blocked": 1})
        self.login(self.karim)  # only his own project
        self.assertEqual(card_numbers(self.client.get("/")), {
            "planned": 0, "in_progress": 1, "on_hold": 0, "completed": 0, "late": 1, "behind": 0, "blocked": 1})

    def test_admins_see_everything_except_cancelled_projects(self):
        self.login(self.amina)
        self.assertEqual(card_numbers(self.client.get("/")), {
            "planned": 1, "in_progress": 4, "on_hold": 1, "completed": 1, "late": 3, "behind": 1, "blocked": 2})

    def test_each_card_number_is_the_number_of_rows_of_the_list_it_opens(self):
        for person in (self.amina, self.omar, self.sara, self.karim):
            self.login(person)
            home = self.client.get("/")
            numbers = card_numbers(home)
            for key in CARDS:
                with self.subTest(person=person.username, card=key):
                    link = card_link(home, key)
                    self.assertEqual(link, f"/projects/?status={key}")
                    rows = list_rows(self.client.get(link))
                    self.assertEqual(len(rows), numbers[key])
                    self.assertNotIn(self.cancelled.pk, rows)

    def test_late_and_behind_never_list_the_same_project(self):
        self.login(self.amina)
        late = set(list_rows(self.client.get("/projects/?status=late")))
        behind = set(list_rows(self.client.get("/projects/?status=behind")))
        self.assertIn(self.late_and_behind.pk, late)
        self.assertEqual(late & behind, set())

    def test_the_cards_cost_the_same_number_of_queries_however_many_projects(self):
        self.login(self.amina)

        def queries_for_the_home_page():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get("/").status_code, 200)
            return len(queries)

        before = queries_for_the_home_page()
        for number in range(6):  # late, behind schedule and blocked projects
            project = Project.objects.create(name=f"More {number}", manager=self.omar, status="in_progress",
                                             start_date=days_from_today(-50),
                                             end_date=days_from_today(-1 if number % 2 else 50))
            Task.objects.create(project=project, title="Late", due_date=days_from_today(-3))
            Task.objects.create(project=project, title="Stuck", status="blocked")
        self.assertEqual(card_numbers(self.client.get("/"))["blocked"], 8)
        self.assertEqual(queries_for_the_home_page(), before)


class CardPageTests(CardsTestCase):
    def setUp(self):
        self.login(self.omar)
        self.response = self.client.get("/")

    def test_the_personal_cards_stay_and_the_new_row_has_its_own_title(self):
        html = self.response.content.decode()
        for text in ("Active projects", "My open tasks", "My overdue tasks", "My next tasks"):
            self.assertIn(text, html)
        self.assertIn('<h3 class="mb-0 me-auto" id="glance-title">Projects at a glance</h3>', html)
        # The personal cards come first, then the projects at a glance
        self.assertLess(html.index("My overdue tasks"), html.index("Projects at a glance"))

    def test_the_cards_say_that_they_overlap(self):
        self.assertContains(self.response, "A project counts in its status and also in Late, Behind schedule or "
                                           "Blocked when they apply, so the numbers overlap. Cancelled projects "
                                           "aren't counted.")

    def test_a_link_to_the_dashboard(self):
        self.assertContains(self.response, '<a href="/dashboard/" class="btn btn-sm">')
        self.assertContains(self.response, "See the dashboard")

    def test_the_colour_always_comes_with_an_icon_and_a_word(self):
        html = self.response.content.decode()
        expected = {
            "late": ('<i class="ti ti-alert-triangle text-red me-1"></i>Late', "End date passed"),
            "behind": ('<i class="ti ti-clock-exclamation text-orange me-1"></i>Behind schedule',
                       "More than 10 points behind the plan"),
            "blocked": ('<i class="ti ti-hand-stop text-red me-1"></i>Blocked', "At least one task is blocked"),
        }
        for key, (label, hint) in expected.items():
            card = re.search(rf'id="card-{key}">.*?</a>', html, re.S).group(0)
            self.assertIn(label, card)
            self.assertIn(hint, card)
        planned = re.search(r'id="card-planned">.*?</a>', html, re.S).group(0)
        self.assertIn('<span class="badge bg-purple-lt">Planned</span>', planned)


class ProjectListFilterTests(CardsTestCase):
    def setUp(self):
        self.login(self.omar)

    def test_the_filter_offers_the_projects_that_need_attention(self):
        response = self.client.get("/projects/?status=behind")
        self.assertContains(response, '<optgroup label="Projects that need attention">')
        self.assertContains(response, '<option value="late" >Late</option>', html=True)
        self.assertContains(response, '<option value="behind" selected>Behind schedule</option>', html=True)
        self.assertContains(response, '<option value="blocked" >Blocked</option>', html=True)

    def test_each_filter_shows_the_right_projects(self):
        self.assertEqual(set(list_rows(self.client.get("/projects/?status=late"))),
                         {self.planned_late.pk, self.late_and_behind.pk})
        self.assertEqual(list_rows(self.client.get("/projects/?status=behind")), [self.behind.pk])
        self.assertEqual(list_rows(self.client.get("/projects/?status=blocked")), [self.blocked.pk])

    def test_the_filter_works_with_the_search_box(self):
        self.assertEqual(list_rows(self.client.get("/projects/?status=late&q=planned")), [self.planned_late.pk])

    def test_the_excel_export_follows_the_filter(self):
        response = self.client.get("/projects/export.xlsx?status=late")
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sorted(row[0] for row in sheet.iter_rows(min_row=2, values_only=True)),
                         ["Late and behind", "Planned late"])

    def test_an_unknown_filter_shows_the_active_projects(self):
        response = self.client.get("/projects/?status=nonsense")
        self.assertNotIn(self.completed.pk, list_rows(response))
        self.assertIn(self.blocked.pk, list_rows(response))


class DashboardCardTests(CardsTestCase):
    def test_the_dashboard_counts_the_projects_behind_schedule(self):
        self.login(self.omar)
        html = self.client.get("/dashboard/").content.decode()
        card = re.search(r'</i>Behind schedule\s*</div>\s*<div class="h1 mb-0">(\d+)</div>', html)
        self.assertEqual(card.group(1), "1")
        late = re.search(r'</i>Late projects\s*</div>\s*<div class="h1 mb-0">(\d+)</div>', html)
        self.assertEqual(late.group(1), "2")


class CardLanguageTests(CardsTestCase):
    def test_in_arabic(self):
        self.omar.language = "ar"
        self.omar.save()
        self.login(self.omar)
        response = self.client.get("/")
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, "المشاريع في لمحة")
        self.assertContains(response, "عرض لوحة المعلومات")
        self.assertContains(response, "متأخر بأكثر من 10 نقاط عن الخطة")
        self.assertContains(response, '<i class="ti ti-hand-stop text-red me-1"></i>متوقف')
        self.assertEqual(card_numbers(response)["late"], 2)
        listed = self.client.get("/projects/?status=blocked")
        self.assertContains(listed, '<option value="blocked" selected>متوقف</option>', html=True)

    def test_in_french(self):
        self.omar.language = "fr"
        self.omar.save()
        self.login(self.omar)
        response = self.client.get("/")
        self.assertContains(response, "Projets en un coup d'œil")
        self.assertContains(response, "Plus de 10 points de retard sur le planning")
        self.assertContains(response, '<i class="ti ti-hand-stop text-red me-1"></i>Bloqué')
