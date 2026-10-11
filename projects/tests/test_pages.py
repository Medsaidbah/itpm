"""The pages show the right things: project list, home, My tasks, dashboard, board and timeline."""

from projects.models import Project, Task

from .base import TeamTestCase, days_from_today


class ProjectListTests(TeamTestCase):
    def setUp(self):
        Project.objects.create(name="Old laptops", manager=self.omar, status="completed")
        Project.objects.create(name="Wi-Fi", manager=self.omar, status="planned")
        self.login(self.omar)  # he manages the three projects

    def names(self, address):
        return sorted(project.name for project in self.client.get(address).context["projects"])

    def test_active_projects_by_default(self):
        self.assertEqual(self.names("/projects/"), ["Firewall", "Wi-Fi"])

    def test_status_filter_and_search(self):
        self.assertEqual(self.names("/projects/?status=all"), ["Firewall", "Old laptops", "Wi-Fi"])
        self.assertEqual(self.names("/projects/?status=completed"), ["Old laptops"])
        self.assertEqual(self.names("/projects/?status=all&q=wi"), ["Wi-Fi"])  # not case-sensitive


class MyTasksTests(TeamTestCase):
    def setUp(self):
        make = Task.objects.create
        make(project=self.project, title="Late one", assignee=self.sara, due_date=days_from_today(-3))
        make(project=self.project, title="Finished", assignee=self.sara, due_date=days_from_today(-3), status="done")
        make(project=self.project, title="Someday", assignee=self.sara)
        make(project=self.project, title="Karim's", assignee=self.karim, due_date=days_from_today(1))
        closed = Project.objects.create(name="Closed", manager=self.omar, status="completed")
        make(project=closed, title="In a closed project", assignee=self.sara, due_date=days_from_today(-1))
        self.login(self.sara)

    def test_sections(self):
        sections = {key: [task.title for task in tasks]
                    for key, label, icon, tasks in self.client.get("/my-tasks/").context["sections"]}
        self.assertEqual(sections, {
            "overdue": ["Late one"],
            "this_week": ["Install the firewall"],  # due in 5 days
            "later": [],
            "no_date": ["Someday"],
        })

    def test_home_page_numbers(self):
        context = self.client.get("/").context
        self.assertEqual((context["open_tasks"], context["overdue_tasks"]), (3, 1))

    def test_done_button_comes_back_to_my_tasks(self):
        response = self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "done", "next": "/my-tasks/"})
        self.assertRedirects(response, "/my-tasks/")

    def test_done_button_never_sends_people_to_another_site(self):
        response = self.client.post(f"/tasks/{self.task.pk}/status/",
                                    {"status": "done", "next": "https://evil.example.com/"})
        self.assertRedirects(response, f"/tasks/{self.task.pk}/")  # back to the task instead


class DashboardTests(TeamTestCase):
    def test_numbers_and_workload(self):  # Omar's projects: only Firewall
        Task.objects.create(project=self.project, title="Late", assignee=self.sara, due_date=days_from_today(-2))
        Task.objects.create(project=self.project, title="Nobody's", due_date=days_from_today(1))
        self.login(self.omar)
        context = self.client.get("/dashboard/").context
        numbers = {kpi["label"]: kpi["value"] for kpi in context["kpis"]}
        self.assertEqual(numbers["Open tasks"], 3)
        self.assertEqual(numbers["Overdue tasks"], 1)
        self.assertEqual(context["workload"], [
            {"name": "Sara Ben", "on_time": 1, "late": 1},
            {"name": "Unassigned", "on_time": 1, "late": 0},  # unassigned work comes last
        ])


class BoardTests(TeamTestCase):
    def test_moving_a_card_saves_its_column_and_order(self):
        other = Task.objects.create(project=self.project, title="Test the VPN", status="in_progress")
        self.login(self.omar)
        # Move "Install" into "In progress", above "Test the VPN"
        response = self.client.post(f"/tasks/{self.task.pk}/move/",
                                    {"status": "in_progress", "order": [self.task.pk, other.pk]})
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.task.status, "in_progress")
        self.assertEqual((self.task.position, other.position), (0, 1))

    def test_unknown_column_is_refused(self):
        self.login(self.omar)
        response = self.client.post(f"/tasks/{self.task.pk}/move/", {"status": "lost"})
        self.assertEqual(response.status_code, 400)


class TimelineTests(TeamTestCase):
    def test_dates_must_make_sense(self):
        self.login(self.omar)
        url = f"/tasks/{self.task.pk}/dates/"
        response = self.client.post(url, {"start_date": "2026-13-45", "due_date": "2026-10-01"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(url, {"start_date": days_from_today(3).isoformat(),
                                          "due_date": days_from_today(1).isoformat()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "The due date can't be before the start date.")
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date, days_from_today(5))  # unchanged

    def test_the_timeline_page_lists_tasks_without_dates_apart(self):
        Task.objects.create(project=self.project, title="No dates yet")
        self.login(self.sara)
        context = self.client.get(f"/projects/{self.project.pk}/timeline/").context
        self.assertEqual([task["name"] for task in context["chart_tasks"]], ["Install the firewall"])
        self.assertEqual([task.title for task in context["undated"]], ["No dates yet"])
