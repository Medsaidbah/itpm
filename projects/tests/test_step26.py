"""Step 26: who wants which emails (My profile), and the email outbox (sent in the background, tried again)."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from projects import outbox
from projects.emails import send_email
from projects.models import EmailOutbox, Notification, Task

from .base import TeamTestCase
from .test_languages import choose

PREFERENCES = ("email_assigned", "email_unblocked", "email_project", "email_reminder")


class PreferencesTests(TeamTestCase):
    def setUp(self):
        User.objects.update(language="en")
        mail.outbox.clear()

    def untick(self, person, *names):
        User.objects.filter(pk=person.pk).update(**{name: False for name in names})

    def test_everybody_gets_every_email_by_default(self):
        self.assertTrue(all(getattr(self.sara, name) for name in PREFERENCES))

    def test_the_profile_shows_the_four_boxes_ticked(self):
        self.login(self.sara)
        page = self.client.get(reverse("profile"))
        self.assertContains(page, "Email notifications")
        for name in PREFERENCES:
            self.assertTrue(page.context["preferences"][name].value(), name)

    def test_saving_the_boxes(self):
        self.login(self.sara)
        response = self.client.post(reverse("notification_settings"), {"email_assigned": "on"}, follow=True)
        self.assertContains(response, "Your email choices are saved.")
        self.sara.refresh_from_db()
        self.assertEqual([getattr(self.sara, name) for name in PREFERENCES], [True, False, False, False])
        self.client.post(reverse("notification_settings"), {name: "on" for name in PREFERENCES})
        self.sara.refresh_from_db()
        self.assertTrue(all(getattr(self.sara, name) for name in PREFERENCES))

    def test_the_choices_are_only_mine(self):
        self.login(self.sara)
        self.client.post(reverse("notification_settings"), {})
        self.omar.refresh_from_db()
        self.assertTrue(all(getattr(self.omar, name) for name in PREFERENCES))

    def test_saving_the_phone_number_does_not_touch_the_choices(self):
        self.untick(self.sara, "email_reminder")
        self.login(self.sara)
        self.client.post(reverse("profile"), {"phone": "+222 22 12 34 56"})
        self.sara.refresh_from_db()
        self.assertFalse(self.sara.email_reminder)

    def test_no_assignment_email_when_unticked_but_the_bell_works(self):
        self.untick(self.sara, "email_assigned")
        self.login(self.omar)
        self.client.post(f"/projects/{self.project.pk}/tasks/new/", {
            "title": "Configure the VPN", "status": "todo", "priority": "medium", "assignee": self.sara.pk})
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Notification.objects.filter(recipient=self.sara, kind="assigned").count(), 1)

    def test_no_project_email_when_unticked_but_the_bell_works(self):
        self.untick(self.karim, "email_project")
        self.login(self.omar)
        self.client.post(f"/projects/{self.project.pk}/team/add/", {"person": self.karim.pk})
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Notification.objects.filter(recipient=self.karim, kind="added_to_team").count(), 1)

    def test_no_you_can_start_email_when_unticked_but_the_bell_works(self):
        blocker = Task.objects.create(project=self.project, title="Cabling", assignee=self.omar)
        self.task.depends_on.add(blocker)
        self.untick(self.sara, "email_unblocked")
        self.login(self.omar)
        self.client.post(reverse("task_status", args=[blocker.pk]), {"status": "done"})
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Notification.objects.filter(recipient=self.sara, kind="unblocked").count(), 1)

    def test_one_box_does_not_switch_off_the_others(self):
        self.untick(self.sara, "email_unblocked")
        self.login(self.omar)
        self.client.post(f"/projects/{self.project.pk}/tasks/new/", {
            "title": "Configure the VPN", "status": "todo", "priority": "medium", "assignee": self.sara.pk})
        self.assertEqual(len(mail.outbox), 1)

    def test_the_security_emails_are_always_sent(self):
        self.untick(self.sara, *PREFERENCES)
        self.sara.set_password("OldPassword#123")
        self.sara.save()
        self.login(self.sara)
        new = "BrandNew#Pass987"
        self.client.post(reverse("password_change"), {
            "old_password": "OldPassword#123", "new_password1": new, "new_password2": new})
        self.assertEqual([email.subject for email in mail.outbox], ["Your password was changed"])

    def test_the_daily_reminder_skips_who_unticked_it(self):
        Task.objects.filter(pk=self.task.pk).update(due_date=timezone.localdate() - timedelta(days=2))
        self.untick(self.sara, "email_reminder")
        call_command("send_reminders", stdout=StringIO())
        self.assertNotIn("sara@example.com", [to for email in mail.outbox for to in email.to])
        recipients = [to for email in mail.outbox for to in email.to]
        self.assertIn("omar@example.com", recipients)  # the manager still gets it
        User.objects.filter(pk=self.sara.pk).update(email_reminder=True)
        mail.outbox.clear()
        call_command("send_reminders", stdout=StringIO())
        self.assertIn("sara@example.com", [to for email in mail.outbox for to in email.to])

    def test_the_boxes_are_translated(self):
        for language, text in (("fr", "Notifications par e-mail"), ("ar", "إشعارات البريد الإلكتروني")):
            choose(self.sara, language)
            self.login(self.sara)
            self.assertContains(self.client.get(reverse("profile")), text)


class SynchronousThread:
    """Stands in for threading.Thread: runs the work at once (without closing the test's database connection),
    so a test can look at the result."""

    def __init__(self, target, args=(), daemon=None):
        self.args = args

    def start(self):
        outbox._work(*self.args)


def waiting_row(**changes):
    fields = {"to": "sara@example.com", "subject": "Hello", "text": "Body", "html": "<p>Body</p>", **changes}
    return EmailOutbox.objects.create(**fields)


class OutboxTests(TeamTestCase):
    def setUp(self):
        mail.outbox.clear()

    def row(self):
        return EmailOutbox.objects.get()

    def test_an_email_is_written_then_sent_and_its_text_is_erased(self):
        send_email("sara@example.com", "Hello", "password_changed", {"person": self.sara}, "en")
        self.assertEqual(len(mail.outbox), 1)
        row = self.row()
        self.assertEqual((row.to, row.subject, row.state), ("sara@example.com", "Hello", "sent"))
        self.assertIsNotNone(row.sent_at)
        self.assertEqual((row.text, row.html), ("", ""))  # a reset link must not stay in the database

    def test_when_the_server_says_no_the_error_is_raised_and_the_email_is_not_tried_again(self):
        with mock.patch.object(mail.EmailMultiAlternatives, "send", side_effect=ConnectionRefusedError("down")):
            with self.assertRaises(ConnectionRefusedError):
                send_email("sara@example.com", "Hello", "password_changed", {"person": self.sara}, "en")
        row = self.row()
        self.assertEqual(row.state, "failed")
        self.assertIn("down", row.last_error)
        self.assertEqual(outbox.waiting_to_retry(), [])

    @override_settings(EMAIL_IN_BACKGROUND=True)
    def test_in_the_app_nothing_is_sent_during_the_page_but_after_it(self):
        with mock.patch("projects.outbox.threading.Thread", SynchronousThread):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                send_email("sara@example.com", "Hello", "password_changed", {"person": self.sara}, "en")
            self.assertEqual(mail.outbox, [])  # the page is done without waiting for the mail server
            self.assertEqual(self.row().state, "waiting")
            for callback in callbacks:  # the page's data is saved: the sender starts
                callback()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.row().state, "sent")

    @override_settings(EMAIL_IN_BACKGROUND=True)
    def test_in_the_app_a_failing_server_never_breaks_the_page(self):
        with mock.patch("projects.outbox.threading.Thread", SynchronousThread):
            with mock.patch.object(mail.EmailMultiAlternatives, "send", side_effect=ConnectionRefusedError("down")):
                with self.captureOnCommitCallbacks(execute=True):
                    send_email("sara@example.com", "Hello", "password_changed", {"person": self.sara}, "en")
        row = self.row()
        self.assertEqual((row.state, row.attempts), ("waiting", 1))
        self.assertIn("down", row.last_error)

    def test_a_failed_email_is_tried_again_after_its_waiting_time(self):
        row = waiting_row(attempts=1, last_attempt_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(outbox.retry_waiting(), 0)  # too early: 5 minutes after the first failure
        self.assertEqual(mail.outbox, [])
        EmailOutbox.objects.filter(pk=row.pk).update(last_attempt_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(outbox.retry_waiting(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.row().state, "sent")

    def test_the_waiting_time_grows_with_each_failure(self):
        waiting_row(attempts=3, last_attempt_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(outbox.waiting_to_retry(), [])  # 3 failures: 15 minutes
        EmailOutbox.objects.update(last_attempt_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(len(outbox.waiting_to_retry()), 1)

    def test_an_email_nobody_tried_to_send_is_found_again(self):
        row = waiting_row()
        self.assertEqual(outbox.waiting_to_retry(), [])  # it was just written: its sender is on the way
        EmailOutbox.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(minutes=3))
        self.assertEqual(outbox.waiting_to_retry(), [row.pk])

    def test_it_gives_up_after_the_last_try(self):
        row = waiting_row(attempts=settings.EMAIL_MAX_ATTEMPTS - 1, last_attempt_at=timezone.now() - timedelta(days=1))
        with mock.patch.object(mail.EmailMultiAlternatives, "send", side_effect=ConnectionRefusedError("down")):
            self.assertFalse(outbox.deliver(row.pk))
        self.assertEqual(self.row().state, "failed")
        self.assertFalse(outbox.deliver(row.pk))  # not tried again
        self.assertEqual(mail.outbox, [])

    def test_an_email_is_never_sent_twice(self):
        row = waiting_row()
        self.assertTrue(outbox.deliver(row.pk))
        self.assertFalse(outbox.deliver(row.pk))
        self.assertEqual(len(mail.outbox), 1)

    def test_an_email_being_sent_by_someone_else_is_left_alone_until_it_is_stuck(self):
        row = waiting_row(claimed_at=timezone.now() - timedelta(minutes=1))
        self.assertFalse(outbox.deliver(row.pk))
        EmailOutbox.objects.filter(pk=row.pk).update(claimed_at=timezone.now() - timedelta(minutes=11))
        self.assertTrue(outbox.deliver(row.pk))

    def test_old_rows_are_deleted(self):
        long_ago = timezone.now() - timedelta(days=40)
        old_sent = waiting_row(sent_at=long_ago)
        recent_sent = waiting_row(sent_at=timezone.now() - timedelta(days=2))
        old_failed = waiting_row(attempts=settings.EMAIL_MAX_ATTEMPTS)
        recent_failed = waiting_row(attempts=settings.EMAIL_MAX_ATTEMPTS)
        EmailOutbox.objects.filter(pk=old_failed.pk).update(created_at=timezone.now() - timedelta(days=8))
        self.assertEqual(outbox.purge(), 2)
        self.assertEqual(set(EmailOutbox.objects.values_list("pk", flat=True)), {recent_sent.pk, recent_failed.pk})
        del old_sent

    def test_the_command_sends_again_and_cleans(self):
        waiting_row(attempts=1, last_attempt_at=timezone.now() - timedelta(minutes=30))
        out = StringIO()
        call_command("send_outbox", stdout=out)
        self.assertIn("1 email(s) sent again", out.getvalue())
        self.assertIn("0 waiting, 0 failed, 1 sent", out.getvalue())
        self.assertEqual(len(mail.outbox), 1)

    def test_the_command_can_only_show_the_numbers(self):
        waiting_row(attempts=1, last_attempt_at=timezone.now() - timedelta(minutes=30))
        out = StringIO()
        call_command("send_outbox", "--status", stdout=out)
        self.assertIn("1 waiting, 0 failed, 0 sent", out.getvalue())
        self.assertEqual(mail.outbox, [])

    def test_the_reminder_job_still_reports_a_mail_server_that_is_down(self):
        Task.objects.filter(pk=self.task.pk).update(due_date=timezone.localdate() - timedelta(days=2))
        with mock.patch.object(mail.EmailMultiAlternatives, "send", side_effect=ConnectionRefusedError("down")):
            with self.assertRaises(Exception) as caught:
                call_command("send_reminders", "--user", "sara", stdout=StringIO(), stderr=StringIO())
        self.assertIn("couldn't be sent", str(caught.exception))


class AdminEmailsTests(TeamTestCase):
    def setUp(self):
        mail.outbox.clear()
        self.sent = waiting_row(sent_at=timezone.now(), text="", html="")
        self.failed = waiting_row(attempts=settings.EMAIL_MAX_ATTEMPTS, last_error="Authentication unsuccessful")
        self.waiting = waiting_row(attempts=1)

    def test_only_admins_see_it(self):
        for person in (self.omar, self.sara, self.karim):
            self.login(person)
            self.assertEqual(self.client.get(reverse("admin_emails")).status_code, 403)
            self.assertEqual(self.client.post(reverse("admin_emails_retry")).status_code, 403)
        self.assertEqual(mail.outbox, [])

    def test_the_page_lists_the_emails_and_the_reason_of_a_failure(self):
        self.login(self.amina)
        page = self.client.get(reverse("admin_emails"))
        self.assertContains(page, "Authentication unsuccessful")
        self.assertContains(page, "1 waiting")
        self.assertContains(page, "1 given up")
        self.assertEqual(len(page.context["rows"]), 3)

    def test_the_overview_counts_the_failures(self):
        self.login(self.amina)
        self.assertEqual(self.client.get(reverse("admin_home")).context["emails_failed"], 1)

    def test_send_again_gives_the_failed_ones_new_tries(self):
        self.login(self.amina)
        response = self.client.post(reverse("admin_emails_retry"), follow=True)
        self.assertContains(response, "2 emails sent.")
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual({row.state for row in EmailOutbox.objects.all()}, {"sent"})

    @override_settings(EMAIL_IN_BACKGROUND=True)
    def test_send_again_in_the_app_starts_the_sender(self):
        self.login(self.amina)
        with mock.patch("projects.outbox.threading.Thread", SynchronousThread):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("admin_emails_retry"))
        self.assertEqual(len(mail.outbox), 2)

    def test_an_empty_outbox_says_so(self):
        EmailOutbox.objects.all().delete()
        self.login(self.amina)
        self.assertContains(self.client.get(reverse("admin_emails")), "No email sent yet.")

    def test_the_page_opens_in_every_language(self):
        for language in ("fr", "ar"):
            choose(self.amina, language)
            self.login(self.amina)
            self.assertEqual(self.client.get(reverse("admin_emails")).status_code, 200)
