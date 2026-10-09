"""What most tests start from: a small team and one project."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from projects.models import Project, Task


def days_from_today(days):
    """A date some days from today: days_from_today(-2) is the day before yesterday."""
    return timezone.localdate() + timedelta(days=days)


class TeamTestCase(TestCase):
    """Each test in a class built on this one starts with:
    - Amina (admin), Omar (manager), Sara and Karim (members)
    - the project "Firewall", managed by Omar
    - the task "Install the firewall" in it, assigned to Sara, due in 5 days.
    Whatever a test changes is undone after it, so tests can't disturb each other."""

    @classmethod
    def setUpTestData(cls):
        # No passwords: the tests log people in with force_login(), which is much faster
        cls.amina = User.objects.create_superuser("amina", "amina@example.com", first_name="Amina")
        cls.omar = User.objects.create_user("omar", "omar@example.com", role="manager",
                                            first_name="Omar", last_name="Ali")
        cls.sara = User.objects.create_user("sara", "sara@example.com", first_name="Sara", last_name="Ben")
        cls.karim = User.objects.create_user("karim", "karim@example.com", first_name="Karim")

        cls.project = Project.objects.create(name="Firewall", manager=cls.omar, status="in_progress")
        cls.task = Task.objects.create(project=cls.project, title="Install the firewall",
                                       assignee=cls.sara, due_date=days_from_today(5))

    def login(self, user):
        """Log this person in, without going through the login page."""
        self.client.force_login(user)

    def task_form_data(self, task=None, **changes):
        """What the task form sends for a task (by default the main one), with some changes."""
        task = task or self.task
        data = {
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "assignee": task.assignee_id or "",
            "start_date": task.start_date or "",
            "due_date": task.due_date or "",
            "parent": task.parent_id or "",
        }
        data.update(changes)
        return data
