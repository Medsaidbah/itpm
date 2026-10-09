"""Who can do what. These are the most important rules of the app:
- you must be logged in to see anything;
- everyone sees all projects and tasks;
- admins and managers create and edit projects and tasks, and see the dashboard;
- members change the status of the tasks assigned to them;
- everyone can comment; people delete their own comments.
The server checks these rules itself: hiding a button is not enough."""

from projects.models import Comment, Project, Task

from .base import TeamTestCase, days_from_today


class LoginRequiredTests(TeamTestCase):
    def test_pages_need_a_login(self):
        pages = [
            "/",
            "/projects/",
            f"/projects/{self.project.pk}/",
            f"/projects/{self.project.pk}/board/",
            f"/projects/{self.project.pk}/timeline/",
            f"/projects/{self.project.pk}/export.xlsx",
            f"/tasks/{self.task.pk}/",
            "/my-tasks/",
            "/activity/",
            "/dashboard/",
            "/reminders/preview/",
        ]
        for page in pages:
            with self.subTest(page=page):  # if one page fails, the message says which one
                self.assertRedirects(self.client.get(page), f"/login/?next={page}")


class ProjectPermissionTests(TeamTestCase):
    def test_everyone_sees_all_projects(self):
        self.login(self.karim)  # Karim has no task in this project
        self.assertContains(self.client.get("/projects/"), "Firewall")
        self.assertEqual(self.client.get(f"/projects/{self.project.pk}/").status_code, 200)

    def test_member_cannot_create_or_edit_a_project(self):
        self.login(self.sara)
        new_project = {"name": "Sneaky", "status": "planned", "priority": "low", "manager": self.omar.pk}
        self.assertEqual(self.client.get("/projects/new/").status_code, 403)
        self.assertEqual(self.client.post("/projects/new/", new_project).status_code, 403)
        self.assertEqual(self.client.post(f"/projects/{self.project.pk}/edit/", new_project).status_code, 403)
        self.assertFalse(Project.objects.filter(name="Sneaky").exists())

    def test_manager_can_create_and_edit_a_project(self):
        self.login(self.omar)
        response = self.client.post("/projects/new/", {
            "name": "Wi-Fi", "status": "planned", "priority": "high", "manager": self.omar.pk})
        wifi = Project.objects.get(name="Wi-Fi")
        self.assertRedirects(response, f"/projects/{wifi.pk}/")

        self.client.post(f"/projects/{wifi.pk}/edit/", {
            "name": "Wi-Fi 7", "status": "in_progress", "priority": "high", "manager": self.omar.pk})
        wifi.refresh_from_db()  # read it again from the database
        self.assertEqual((wifi.name, wifi.status), ("Wi-Fi 7", "in_progress"))


class TaskPermissionTests(TeamTestCase):
    def test_member_cannot_create_edit_or_delete_tasks(self):
        self.login(self.sara)
        self.assertEqual(self.client.get(f"/projects/{self.project.pk}/tasks/new/").status_code, 403)
        self.assertEqual(self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(title="Mine now")).status_code, 403)
        self.assertEqual(self.client.post(f"/tasks/{self.task.pk}/delete/").status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Install the firewall")  # nothing changed

    def test_manager_can_edit_and_delete_tasks(self):
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(title="Install it"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Install it")

        response = self.client.post(f"/tasks/{self.task.pk}/delete/")
        self.assertRedirects(response, f"/projects/{self.project.pk}/")
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())

    def test_assignee_can_change_status(self):
        self.login(self.sara)
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "in_progress"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "in_progress")

    def test_other_member_cannot_change_status(self):
        self.login(self.karim)
        response = self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "done"})
        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "todo")

    def test_board_follows_the_same_rule(self):
        url = f"/tasks/{self.task.pk}/move/"
        self.login(self.karim)
        response = self.client.post(url, {"status": "done", "order": [self.task.pk]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "You can only move tasks assigned to you.")

        self.login(self.sara)
        self.assertEqual(self.client.post(url, {"status": "done", "order": [self.task.pk]}).status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "done")

    def test_only_managers_change_dates_on_the_timeline(self):
        url = f"/tasks/{self.task.pk}/dates/"
        dates = {"start_date": days_from_today(1).isoformat(), "due_date": days_from_today(3).isoformat()}
        self.login(self.sara)
        self.assertEqual(self.client.post(url, dates).status_code, 403)
        self.login(self.omar)
        self.assertEqual(self.client.post(url, dates).status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date, days_from_today(3))

    def test_dashboard_is_for_admins_and_managers(self):
        self.login(self.sara)
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)
        for person in (self.omar, self.amina):
            self.login(person)
            self.assertEqual(self.client.get("/dashboard/").status_code, 200)


class CommentPermissionTests(TeamTestCase):
    def test_everyone_can_comment_on_any_task(self):
        self.login(self.karim)
        self.client.post(f"/tasks/{self.task.pk}/comments/add/", {"body": "I can help."})
        self.assertTrue(Comment.objects.filter(task=self.task, author=self.karim, body="I can help.").exists())

    def test_people_delete_their_own_comments_only(self):
        comment = Comment.objects.create(task=self.task, author=self.sara, body="Done soon")
        self.login(self.karim)
        self.assertEqual(self.client.post(f"/comments/{comment.pk}/delete/").status_code, 403)
        self.login(self.sara)
        self.client.post(f"/comments/{comment.pk}/delete/")
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())

    def test_managers_can_delete_any_comment(self):
        comment = Comment.objects.create(task=self.task, author=self.sara, body="Off topic")
        self.login(self.omar)
        self.client.post(f"/comments/{comment.pk}/delete/")
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())
