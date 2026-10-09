"""Emails: the daily reminder and the "New task for you" email.
During tests, Django keeps the emails in a list (mail.outbox) instead of sending them."""

from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import CommandError, call_command
from django.test import override_settings

from accounts.models import User
from projects.emails import reminder_for
from projects.models import Task

from .base import TeamTestCase, days_from_today


@override_settings(SITE_URL="https://itpm.example.org")
class ReminderTests(TeamTestCase):
    def setUp(self):
        make = Task.objects.create
        self.late = make(project=self.project, title="Fix R&D <access>", assignee=self.sara,
                         due_date=days_from_today(-2))
        make(project=self.project, title="Due today", assignee=self.sara, due_date=days_from_today(0))
        make(project=self.project, title="Done already", assignee=self.sara, due_date=days_from_today(-1),
             status="done")
        make(project=self.project, title="Nobody's", due_date=days_from_today(-1))

    def run_command(self, *options):
        output = StringIO()
        call_command("send_reminders", *options, stdout=output)
        return output.getvalue()

    def test_what_a_member_is_told(self):
        reminder = reminder_for(self.sara, days_from_today(0))
        sections = {s.title: [task.title for task in s.tasks] for s in reminder["sections"]}
        # "Install the firewall" is due in 5 days: not yet
        self.assertEqual(sections, {"Overdue": ["Fix R&D <access>"], "Due in the next 3 days": ["Due today"]})
        self.assertEqual(reminder["subject"], "Your tasks: 1 overdue, 1 due soon")

    def test_managers_also_hear_about_late_tasks_in_their_projects(self):
        Task.objects.create(project=self.project, title="Omar's own", assignee=self.omar,
                            due_date=days_from_today(-1))
        sections = {s.title: [task.title for task in s.tasks]
                    for s in reminder_for(self.omar, days_from_today(0))["sections"]}
        self.assertEqual(sections, {
            "Overdue": ["Omar's own"],  # his own late task, told only once
            "Late in projects you manage": ["Fix R&D <access>", "Nobody's"],
        })

    def test_the_command_emails_only_people_with_something_to_do(self):
        output = self.run_command()
        self.assertEqual(sorted(email.to[0] for email in mail.outbox), ["omar@example.com", "sara@example.com"])
        self.assertIn("2 emails sent.", output)

        email = next(email for email in mail.outbox if email.to == ["sara@example.com"])
        self.assertEqual(email.subject, "Your tasks: 1 overdue, 1 due soon")
        # The plain-text version keeps the title as it is; the HTML version makes it safe
        self.assertIn("- Fix R&D <access> (Firewall): 2 days late", email.body)
        self.assertIn(f"https://itpm.example.org/tasks/{self.late.pk}/", email.body)
        html = email.alternatives[0][0]
        self.assertIn("Fix R&amp;D &lt;access&gt;", html)

    def test_people_without_an_email_address_are_skipped(self):
        User.objects.filter(pk=self.sara.pk).update(email="")
        output = self.run_command()
        self.assertIn("sara: no email address, skipped", output)
        self.assertEqual([email.to for email in mail.outbox], [["omar@example.com"]])

    def test_dry_run_and_one_person(self):
        output = self.run_command("--dry-run")
        self.assertIn('sara <sara@example.com>: would get "Your tasks: 1 overdue, 1 due soon"', output)
        self.assertEqual(mail.outbox, [])  # nothing sent

        self.run_command("--user", "sara")
        self.assertEqual([email.to for email in mail.outbox], [["sara@example.com"]])
        with self.assertRaises(CommandError):
            self.run_command("--user", "nobody")


@override_settings(SITE_URL="https://itpm.example.org")
class NewTaskEmailTests(TeamTestCase):
    def new_task(self, **changes):
        data = {"title": "Configure the VPN", "status": "todo", "priority": "high",
                "assignee": self.sara.pk, "due_date": days_from_today(4)}
        data.update(changes)
        return self.client.post(f"/projects/{self.project.pk}/tasks/new/", data, follow=True)

    def test_the_new_assignee_gets_an_email(self):
        self.login(self.omar)
        self.new_task()
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual((email.to, email.subject), (["sara@example.com"], "New task for you: Configure the VPN"))
        self.assertIn('Omar Ali gave you a task in "Firewall"', email.body)

    def test_no_email_to_yourself_or_when_nothing_changes(self):
        self.login(self.omar)
        self.new_task(assignee=self.omar.pk)  # Omar gives the task to himself
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(priority="high"))  # still Sara's
        self.assertEqual(mail.outbox, [])

    def test_giving_a_task_to_someone_else_sends_an_email(self):
        self.login(self.omar)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(assignee=self.karim.pk))
        self.assertEqual([email.to for email in mail.outbox], [["karim@example.com"]])

    def test_the_task_is_saved_even_if_the_mail_server_is_down(self):
        self.login(self.omar)
        with mock.patch.object(mail.EmailMultiAlternatives, "send", side_effect=ConnectionRefusedError):
            with self.assertLogs("projects.emails", level="ERROR"):  # the error is written in the log
                response = self.new_task()
        self.assertTrue(Task.objects.filter(title="Configure the VPN").exists())
        self.assertContains(response, "The task is saved, but the email to Sara Ben couldn&#x27;t be sent.")
