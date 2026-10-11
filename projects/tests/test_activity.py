"""The Activity page: who did what, and when."""

import re

from django.utils.formats import date_format

from projects.models import Comment, Project, Task

from .base import TeamTestCase, days_from_today


def visible_text(response):
    """The words of a page, without the HTML tags, on one line."""
    html = re.sub(r"<script.*?</script>", " ", response.content.decode(), flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class ActivityTests(TeamTestCase):
    def test_changes_are_told_newest_first(self):
        # Everything goes through the pages, so the history knows who did it
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(due_date=days_from_today(9)))
        self.login(self.sara)
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "in_progress"})
        self.client.post(f"/tasks/{self.task.pk}/comments/add/", {"body": "Started today"})

        text = visible_text(self.client.get("/activity/"))
        expected_in_order = [
            "Sara Ben commented on Install the firewall",
            "Started today",
            "Sara Ben updated the task Install the firewall",
            "Status: To do → In progress",
            "Omar Ali updated the task Install the firewall",
            f"Due date: {date_format(days_from_today(5), 'd M Y')} → {date_format(days_from_today(9), 'd M Y')}",
        ]
        position = 0
        for words in expected_in_order:
            position = text.find(words, position)
            self.assertNotEqual(position, -1, f'"{words}" is missing or in the wrong order')

    def test_saving_without_changes_is_not_shown(self):
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data())  # same values
        self.client.post(f"/projects/{self.project.pk}/edit/", {
            "name": "Firewall", "status": "in_progress", "priority": "medium", "manager": self.omar.pk})
        self.assertEqual(self.task.history.count(), 2)  # the saves were recorded...
        text = visible_text(self.client.get("/activity/"))
        self.assertNotIn("updated the task", text)  # ...but there is nothing to tell
        self.assertNotIn("updated the project", text)

    def test_filters(self):
        self.project.add_to_team(self.karim)
        other = Task.objects.create(project=self.project, title="Cabling")
        Comment.objects.create(task=other, author=self.karim, body="Cables ordered")
        Comment.objects.create(task=self.task, author=self.sara, body="Rack ready")
        self.login(self.sara)

        by_person = visible_text(self.client.get(f"/activity/?person={self.karim.pk}"))
        self.assertIn("Cables ordered", by_person)
        self.assertNotIn("Rack ready", by_person)

        by_task = visible_text(self.client.get(f"/activity/?task={self.task.pk}"))
        self.assertIn("Rack ready", by_task)
        self.assertNotIn("Cables ordered", by_task)

    def test_a_task_moved_from_another_project_keeps_its_old_values_secret(self):
        wifi = Project.objects.create(name="Wi-Fi", manager=self.karim)
        task = Task.objects.create(project=wifi, title="Secret plan")
        task.project, task.title = self.project, "Plain plan"  # moved to Firewall (before Stage 16c)
        task.save()
        self.login(self.sara)
        text = visible_text(self.client.get("/activity/"))
        self.assertIn("updated the task Plain plan", text)
        self.assertNotIn("Secret plan", text)

    def test_deleted_tasks_have_no_link(self):
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/delete/")
        response = self.client.get("/activity/")
        self.assertIn("Omar Ali deleted the task Install the firewall", visible_text(response))
        self.assertNotContains(response, f'href="/tasks/{self.task.pk}/"')

    def test_comments_cannot_inject_html(self):
        Comment.objects.create(task=self.task, author=self.sara, body="<script>alert('hi')</script>")
        self.login(self.omar)
        response = self.client.get("/activity/")
        self.assertNotContains(response, "<script>alert('hi')</script>")
        self.assertContains(response, "&lt;script&gt;")  # shown as text, not run
