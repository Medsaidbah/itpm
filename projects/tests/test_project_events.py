"""Step 23: people are told (bell and email) when their place in a project changes:
added to the team, made manager, given the manager role (co-manager), or no longer manager."""

import importlib
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.core import mail
from django.db import IntegrityError, transaction
from django.urls import reverse

from accounts.models import User
from projects.models import Notification, Project, ProjectMember

from .base import TeamTestCase
from .test_languages import choose

migration = importlib.import_module("projects.migrations.0007_project_notifications")


class ProjectEventTestCase(TeamTestCase):
    def setUp(self):
        self.lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina", last_name="Diallo")
        self.url = f"/projects/{self.project.pk}/"
        User.objects.update(language="en")  # unless a test says otherwise, everybody reads English
        mail.outbox.clear()

    def notes(self, person, kind=None):
        found = Notification.objects.filter(recipient=person, project__isnull=False)
        return found.filter(kind=kind) if kind else found

    def add_to_team(self, who, person):
        self.login(who)
        return self.client.post(f"{self.url}team/add/", {"person": person.pk})


class AddedToTeamTests(ProjectEventTestCase):
    def test_the_new_member_gets_an_email_and_a_bell_entry(self):
        choose(self.lina, "en")
        self.add_to_team(self.omar, self.lina)
        email, = mail.outbox
        self.assertEqual(email.to, ["lina@example.com"])
        self.assertEqual(email.subject, "You were added to the project: Firewall")
        self.assertIn("Omar Ali added you to the team of the project “Firewall”.", email.body)
        self.assertIn(f"{settings.SITE_URL}{self.url}", email.body)
        note, = self.notes(self.lina)
        self.assertEqual(
            (note.kind, note.project, note.task, note.actor), ("added_to_team", self.project, None, self.omar)
        )

    def test_admins_adding_from_the_administration_pages_tell_them_too(self):
        self.login(self.amina)
        self.client.post(f"/administration/users/{self.lina.pk}/projects/add/", {"project": self.project.pk})
        self.assertEqual([e.to for e in mail.outbox], [["lina@example.com"]])
        self.assertEqual(self.notes(self.lina, "added_to_team").count(), 1)

    def test_nothing_when_the_person_was_in_the_team_already(self):
        self.add_to_team(self.omar, self.sara)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.notes(self.sara).count(), 0)

    def test_nothing_when_someone_joins_a_team_because_a_task_was_given_to_them(self):
        # They get the "new task" email: a second email about the same thing would be noise
        self.login(self.amina)
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(assignee=self.lina.pk))
        self.assertEqual([e.subject for e in mail.outbox], ["New task for you: Install the firewall"])
        self.assertEqual(self.notes(self.lina).count(), 0)

    def test_the_email_follows_the_profile_language(self):
        for language, subject in (("fr", "Vous avez été ajouté(e) au projet : Firewall"),
                                  ("ar", "تمت إضافتك إلى المشروع: Firewall"),
                                  ("", "Vous avez été ajouté(e) au projet : Firewall")):  # blank: the default, French
            with self.subTest(language=language):
                ProjectMember.objects.filter(user=self.lina).delete()
                choose(self.lina, language)
                mail.outbox.clear()
                self.add_to_team(self.omar, self.lina)
                self.assertEqual(mail.outbox[0].subject, subject)

    def test_a_person_without_an_email_address_only_gets_the_bell(self):
        User.objects.filter(pk=self.lina.pk).update(email="")
        self.add_to_team(self.omar, self.lina)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.notes(self.lina).count(), 1)

    def test_the_change_is_saved_even_if_the_mail_server_is_down(self):
        self.login(self.omar)
        with mock.patch.object(mail.EmailMultiAlternatives, "send", side_effect=ConnectionRefusedError):
            with self.assertLogs("projects.emails", level="ERROR"):
                response = self.client.post(f"{self.url}team/add/", {"person": self.lina.pk}, follow=True)
        self.assertTrue(ProjectMember.objects.filter(project=self.project, user=self.lina).exists())
        self.assertEqual(self.notes(self.lina).count(), 1)  # the bell works anyway
        self.assertContains(response, "the email to Lina Diallo couldn&#x27;t be sent")


class ManagerChangeTests(ProjectEventTestCase):
    def test_a_new_project_tells_its_manager(self):
        self.login(self.amina)
        data = {"name": "Wi-Fi", "status": "planned", "priority": "medium", "manager": self.lina.pk}
        self.client.post("/projects/new/", data)
        email, = mail.outbox
        self.assertEqual((email.to, email.subject), (["lina@example.com"], "You are now the manager of: Wi-Fi"))
        self.assertEqual(self.notes(self.lina, "manager").count(), 1)

    def test_an_admin_who_makes_themselves_manager_is_not_told(self):
        self.login(self.amina)
        self.client.post("/projects/new/", {"name": "Wi-Fi", "status": "planned", "priority": "medium",
                                            "manager": self.amina.pk})
        self.assertEqual(mail.outbox, [])

    def test_changing_the_manager_tells_the_new_one_and_the_one_who_steps_down(self):
        self.login(self.amina)
        data = {"name": "Firewall", "status": "in_progress", "priority": "medium", "manager": self.sara.pk}
        self.client.post(f"{self.url}edit/", data)
        sent = {e.to[0]: e.subject for e in mail.outbox}
        self.assertEqual(sent, {
            "sara@example.com": "You are now the manager of: Firewall",
            "omar@example.com": "You are no longer the manager of: Firewall",
        })
        self.assertEqual(self.notes(self.sara, "manager").count(), 1)
        self.assertEqual(self.notes(self.omar, "manager_ended").count(), 1)

    def test_saving_a_project_without_changing_the_manager_tells_nobody(self):
        self.login(self.omar)
        data = {"name": "Firewall 2", "status": "in_progress", "priority": "medium", "manager": self.omar.pk}
        self.client.post(f"{self.url}edit/", data)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Notification.objects.filter(project__isnull=False).count(), 0)


class AdminSavesWithoutChangeTests(ProjectEventTestCase):
    def test_an_admin_saving_a_project_does_not_tell_its_manager_again(self):
        self.login(self.amina)  # not the manager: the "told about yourself" rule can't hide a mistake
        data = {"name": "Firewall 2", "status": "in_progress", "priority": "medium", "manager": self.omar.pk}
        self.client.post(f"{self.url}edit/", data)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Notification.objects.filter(project__isnull=False).count(), 0)


class CoManagerEventTests(ProjectEventTestCase):
    def setUp(self):
        super().setUp()
        self.project.add_to_team(self.lina)

    def give_role(self, who, value="1", person=None):
        self.login(who)
        return self.client.post(f"{self.url}team/{(person or self.lina).pk}/manager/", {"manager": value})

    def test_giving_the_role_tells_the_person(self):
        self.give_role(self.omar)
        email, = mail.outbox
        self.assertEqual(email.subject, "You were given the manager role in: Firewall")
        self.assertIn("Omar Ali gave you the manager role in the project “Firewall”.", email.body)
        self.assertEqual(self.notes(self.lina, "co_manager").count(), 1)

    def test_taking_the_role_away_tells_the_person(self):
        self.give_role(self.omar)
        mail.outbox.clear()
        self.give_role(self.omar, "0")
        email, = mail.outbox
        self.assertEqual(email.subject, "Your manager role was taken away in: Firewall")
        self.assertEqual(self.notes(self.lina, "co_manager_ended").count(), 1)

    def test_giving_up_your_own_role_tells_nobody(self):
        self.give_role(self.omar)
        mail.outbox.clear()
        self.give_role(self.lina, "0")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.notes(self.lina, "co_manager_ended").count(), 0)

    def test_repeating_the_same_choice_tells_nobody_twice(self):
        self.give_role(self.omar)
        self.give_role(self.omar)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.notes(self.lina, "co_manager").count(), 1)

    def test_a_refused_request_tells_nobody(self):
        self.give_role(self.sara)  # an ordinary member: 403
        self.assertEqual(mail.outbox, [])

    def test_a_deactivated_person_is_not_told(self):
        self.give_role(self.omar)
        mail.outbox.clear()
        User.objects.filter(pk=self.lina.pk).update(is_active=False)
        self.give_role(self.omar, "0")
        self.assertEqual(mail.outbox, [])


class BellForProjectEventsTests(ProjectEventTestCase):
    def test_the_list_says_what_happened_for_every_kind(self):
        choose(self.lina, "en")
        self.project.add_to_team(self.lina)
        for kind in Notification.PROJECT_KINDS:
            Notification.objects.create(recipient=self.lina, kind=kind, project=self.project, actor=self.omar)
        self.login(self.lina)
        text = " ".join(self.client.get("/notifications/").content.decode().split())
        for words in ("added you to the team of", "made you the manager of", "gave you the manager role in",
                      "chose another manager for", "took away your manager role in"):
            self.assertIn(words, text)
        self.assertIn("Firewall · just now", text)

    def test_a_click_marks_it_as_read_and_opens_the_project(self):
        self.project.add_to_team(self.lina)
        note = Notification.objects.create(
            recipient=self.lina, kind="added_to_team", project=self.project, actor=self.omar
        )
        self.login(self.lina)
        response = self.client.get(reverse("notification_open", args=[note.pk]))
        self.assertRedirects(response, self.url)
        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)

    def test_other_peoples_do_not_exist_and_a_removed_member_no_longer_sees_it(self):
        self.project.add_to_team(self.lina)
        note = Notification.objects.create(
            recipient=self.lina, kind="added_to_team", project=self.project, actor=self.omar
        )
        self.login(self.sara)
        self.assertEqual(self.client.get(reverse("notification_open", args=[note.pk])).status_code, 404)
        ProjectMember.objects.filter(user=self.lina).delete()
        self.login(self.lina)
        self.assertEqual(self.client.get(reverse("notification_open", args=[note.pk])).status_code, 404)
        self.assertEqual(Notification.for_person(self.lina).count(), 0)

    def test_deleting_the_project_deletes_its_notifications(self):
        Notification.objects.create(recipient=self.sara, kind="added_to_team", project=self.project, actor=self.omar)
        Project.objects.filter(pk=self.project.pk).delete()
        self.assertEqual(Notification.objects.filter(kind="added_to_team").count(), 0)

    def test_a_notification_is_about_a_task_or_a_project_never_both_or_neither(self):
        for fields in ({}, {"task": self.task, "project": self.project}):
            with self.subTest(fields=sorted(fields)), self.assertRaises(IntegrityError), transaction.atomic():
                Notification.objects.create(recipient=self.sara, kind="assigned", actor=self.omar, **fields)

    def test_going_back_in_the_migration_drops_only_the_project_notifications(self):
        Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        Notification.objects.create(recipient=self.sara, kind="manager", project=self.project, actor=self.omar)
        migration.remove_project_notifications(apps, None)
        self.assertEqual(list(Notification.objects.values_list("kind", flat=True)), ["assigned"])


class ArabicProjectEmailTests(ProjectEventTestCase):
    def test_an_arabic_profile_gets_a_right_to_left_email(self):
        choose(self.lina, "ar")
        self.add_to_team(self.omar, self.lina)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('<html lang="ar" dir="rtl">', html)
        self.assertIn("فتح المشروع", html)
