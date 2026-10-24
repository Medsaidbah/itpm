"""Step 24: "forgot my password" by email, the "your password was changed" notice, and the email
(and bell entry) when the last task someone was waiting for is done."""

import re
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from projects.models import Notification, Task

from .base import TeamTestCase
from .test_languages import choose

NEW_PASSWORD = {"new_password1": "BrandNew#Pass987", "new_password2": "BrandNew#Pass987"}


class ForgotPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sara = User.objects.create_user(
            "sara", "sara@example.com", "OldPassword#123", first_name="Sara", language="en")
        mail.outbox.clear()

    def ask(self, email="sara@example.com"):
        return self.client.post(reverse("password_reset"), {"email": email})

    def link_in(self, email):
        found = re.search(r"https?://\S+/password-reset/[^\s/]+/[^\s/]+/", email.body)
        self.assertIsNotNone(found, email.body)
        return found.group(0)

    def test_the_login_page_links_to_it(self):
        self.assertContains(self.client.get(reverse("login")), reverse("password_reset"))

    def test_the_page_opens_without_logging_in(self):
        self.assertEqual(self.client.get(reverse("password_reset")).status_code, 200)

    def test_an_email_with_a_link_is_sent_and_the_link_starts_with_the_site_address(self):
        response = self.ask()
        self.assertRedirects(response, reverse("password_reset_done"))
        email, = mail.outbox
        self.assertEqual(email.to, ["sara@example.com"])
        self.assertEqual(email.subject, "Reset your password")
        self.assertTrue(self.link_in(email).startswith(settings.SITE_URL))

    def test_the_answer_is_the_same_for_an_unknown_address(self):
        response = self.ask("nobody@example.com")
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(mail.outbox, [])

    def test_nothing_for_a_deactivated_account(self):
        User.objects.filter(pk=self.sara.pk).update(is_active=False)
        self.ask()
        self.assertEqual(mail.outbox, [])

    def test_the_email_follows_the_language_of_the_profile(self):
        choose(self.sara, "fr")
        self.ask()
        self.assertEqual(mail.outbox[0].subject, "Réinitialisez votre mot de passe")

    @override_settings(EMAIL_DEFAULT_LANGUAGE="fr")
    def test_a_blank_profile_gets_the_default_email_language(self):
        choose(self.sara, "")
        self.ask()
        self.assertEqual(mail.outbox[0].subject, "Réinitialisez votre mot de passe")

    def test_only_one_email_every_two_minutes_per_account(self):
        self.ask()
        self.ask()
        self.assertEqual(len(mail.outbox), 1)

    def test_a_mail_server_that_is_down_does_not_show_in_the_answer(self):
        with mock.patch("projects.emails.send_email", side_effect=OSError("down")):
            response = self.ask()
        self.assertRedirects(response, reverse("password_reset_done"))

    def test_the_link_lets_her_choose_a_new_password_and_tells_her_by_email(self):
        self.ask()
        link = self.link_in(mail.outbox[0])
        mail.outbox.clear()
        page = self.client.get(link, follow=True)  # Django swaps the token for a session, then shows the form
        self.assertEqual(page.status_code, 200)
        done = self.client.post(page.request["PATH_INFO"], NEW_PASSWORD)
        self.assertRedirects(done, reverse("password_reset_complete"))
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.check_password("BrandNew#Pass987"))
        email, = mail.outbox
        self.assertEqual(email.subject, "Your password was changed")
        self.assertEqual(email.to, ["sara@example.com"])

    def test_a_link_works_once(self):
        self.ask()
        link = self.link_in(mail.outbox[0])
        page = self.client.get(link, follow=True)
        path = page.request["PATH_INFO"]
        self.client.post(path, NEW_PASSWORD)
        again = self.client.get(link, follow=True)
        self.assertContains(again, "This link doesn")

    def test_a_wrong_link_is_refused_politely(self):
        page = self.client.get(reverse("password_reset_confirm", args=["abc", "def-123"]), follow=True)
        self.assertContains(page, "This link doesn")

    def test_a_weak_password_is_refused(self):
        self.ask()
        page = self.client.get(self.link_in(mail.outbox[0]), follow=True)
        response = self.client.post(page.request["PATH_INFO"], {"new_password1": "123", "new_password2": "123"})
        self.assertEqual(response.status_code, 200)
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.check_password("OldPassword#123"))

    def test_a_temporary_password_stops_being_temporary(self):
        User.objects.filter(pk=self.sara.pk).update(must_change_password=True)
        self.ask()
        page = self.client.get(self.link_in(mail.outbox[0]), follow=True)
        self.client.post(page.request["PATH_INFO"], NEW_PASSWORD)
        self.sara.refresh_from_db()
        self.assertFalse(self.sara.must_change_password)


class PasswordChangedNoticeTests(TeamTestCase):
    def setUp(self):
        User.objects.update(language="en")
        self.sara.set_password("OldPassword#123")
        self.sara.save()
        mail.outbox.clear()

    def change(self, new="BrandNew#Pass987"):
        self.login(self.sara)
        return self.client.post(
            reverse("password_change"),
            {"old_password": "OldPassword#123", "new_password1": new, "new_password2": new},
        )

    def test_changing_the_password_sends_a_notice(self):
        self.change()
        email, = mail.outbox
        self.assertEqual(email.to, ["sara@example.com"])
        self.assertEqual(email.subject, "Your password was changed")

    def test_the_notice_follows_the_profile_language(self):
        choose(self.sara, "fr")
        self.change()
        self.assertEqual(mail.outbox[0].subject, "Votre mot de passe a été modifié")

    def test_no_notice_when_the_change_is_refused(self):
        self.change(new="123")
        self.assertEqual(mail.outbox, [])

    def test_a_mail_server_that_is_down_does_not_stop_the_change(self):
        with mock.patch("projects.emails.send_email", side_effect=OSError("down")):
            response = self.change()
        self.assertEqual(response.status_code, 302)
        self.sara.refresh_from_db()
        self.assertTrue(self.sara.check_password("BrandNew#Pass987"))


class UnblockedTests(TeamTestCase):
    """Sara's task "Install the firewall" waits for "Cabling" (Omar's)."""

    def setUp(self):
        User.objects.update(language="en")
        self.cabling = Task.objects.create(project=self.project, title="Cabling", assignee=self.omar)
        self.task.depends_on.add(self.cabling)
        mail.outbox.clear()

    def finish(self, who=None, task=None):
        self.login(who or self.omar)
        return self.client.post(reverse("task_status", args=[(task or self.cabling).pk]), {"status": "done"})

    def unblocked_notes(self):
        return Notification.objects.filter(kind="unblocked")

    def test_the_person_waiting_gets_an_email_and_a_bell_entry(self):
        self.finish()
        email, = mail.outbox
        self.assertEqual(email.to, ["sara@example.com"])
        self.assertEqual(email.subject, "You can start: Install the firewall")
        self.assertIn("Cabling", email.body)
        self.assertIn(f"{settings.SITE_URL}{self.task.get_absolute_url()}", email.body)
        note, = self.unblocked_notes()
        self.assertEqual((note.recipient, note.task, note.actor, note.project), (self.sara, self.task, self.omar, None))

    def test_the_email_follows_the_profile_language(self):
        choose(self.sara, "fr")
        self.finish()
        self.assertEqual(mail.outbox[0].subject, "Vous pouvez commencer : Install the firewall")

    def test_the_bell_page_shows_it_in_every_language(self):
        self.finish()
        for language, text in (("en", "You can start"), ("fr", "Vous pouvez commencer"), ("ar", "يمكنك البدء")):
            choose(self.sara, language)
            self.login(self.sara)
            self.assertContains(self.client.get(reverse("notification_list")), text)

    def test_nothing_while_another_task_is_still_unfinished(self):
        other = Task.objects.create(project=self.project, title="Racking", assignee=self.omar)
        self.task.depends_on.add(other)
        self.finish()
        self.assertEqual(mail.outbox, [])
        self.assertFalse(self.unblocked_notes().exists())
        self.finish(task=other)  # now it was the last one
        self.assertEqual(len(mail.outbox), 1)

    def test_nothing_when_the_person_finishes_the_task_that_unblocks_their_own(self):
        self.cabling.assignee = self.sara
        self.cabling.save()
        self.finish(self.sara)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(self.unblocked_notes().exists())

    def test_nothing_when_the_waiting_task_is_done_already(self):
        Task.objects.filter(pk=self.task.pk).update(status="done")
        self.finish()
        self.assertEqual(mail.outbox, [])

    def test_nothing_when_nobody_is_assigned_or_the_account_is_deactivated(self):
        Task.objects.filter(pk=self.task.pk).update(assignee=None)
        self.finish()
        self.assertEqual(mail.outbox, [])
        Task.objects.filter(pk=self.task.pk).update(assignee=self.sara)
        User.objects.filter(pk=self.sara.pk).update(is_active=False)
        Task.objects.filter(pk=self.cabling.pk).update(status="todo")
        self.finish()
        self.assertEqual(mail.outbox, [])

    def test_not_again_when_the_task_stays_done(self):
        self.finish()
        self.finish()
        self.assertEqual(len(mail.outbox), 1)

    def test_other_statuses_do_not_count(self):
        self.login(self.omar)
        self.client.post(reverse("task_status", args=[self.cabling.pk]), {"status": "in_progress"})
        self.assertEqual(mail.outbox, [])

    def test_moving_the_card_on_the_board_to_done_tells_too(self):
        self.login(self.omar)
        response = self.client.post(reverse("task_move", args=[self.cabling.pk]), {"status": "done"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_editing_the_task_to_done_tells_too(self):
        self.login(self.omar)
        data = self.task_form_data(self.cabling, status="done")
        self.client.post(reverse("task_edit", args=[self.cabling.pk]), data)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_mail_server_that_is_down_keeps_the_change_and_the_bell_entry(self):
        with mock.patch("projects.emails.send_email", side_effect=OSError("down")):
            self.finish()
        self.cabling.refresh_from_db()
        self.assertEqual(self.cabling.status, "done")
        self.assertEqual(self.unblocked_notes().count(), 1)

    def test_a_click_on_the_bell_entry_opens_the_task(self):
        self.finish()
        note = self.unblocked_notes().get()
        self.login(self.sara)
        response = self.client.get(reverse("notification_open", args=[note.pk]))
        self.assertRedirects(response, self.task.get_absolute_url(), fetch_redirect_response=False)
