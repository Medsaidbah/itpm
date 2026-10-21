"""Tests of Stage 19: notifications inside the app (the bell)."""

from datetime import timedelta

from django.utils import timezone

from accounts.models import User
from projects.models import Notification, ProjectMember, Task

from .base import TeamTestCase


class NotificationTests(TeamTestCase):
    def setUp(self):
        self.lina = User.objects.create_user("lina", "lina@example.com", first_name="Lina")
        ProjectMember.objects.create(project=self.project, user=self.lina)

    def create_task(self, assignee, **changes):
        data = self.task_form_data(self.task, title="Patch the router", assignee=assignee.pk if assignee else "")
        data.update(changes)
        return self.client.post(f"/projects/{self.project.pk}/tasks/new/", data)

    def notes(self, person):
        return Notification.objects.filter(recipient=person)

    # ---- when a notification is made: the real workflow, same rules as the email

    def test_creating_a_task_for_someone_notifies_them(self):
        self.login(self.omar)
        self.create_task(self.lina)
        note, = self.notes(self.lina)
        self.assertEqual((note.kind, note.task.title, note.actor, note.read_at),
                         ("assigned", "Patch the router", self.omar, None))

    def test_changing_the_assignee_notifies_the_new_one_only(self):
        self.login(self.omar)
        data = self.task_form_data(self.task, assignee=self.lina.pk)
        self.client.post(f"/tasks/{self.task.pk}/edit/", data)
        self.assertEqual(self.notes(self.lina).count(), 1)
        self.assertEqual(self.notes(self.sara).count(), 0)

    def test_no_notification_for_self_assignment_unchanged_or_nobody(self):
        self.login(self.omar)
        self.create_task(self.omar)  # to himself
        self.create_task(None)  # to nobody
        self.client.post(f"/tasks/{self.task.pk}/edit/", self.task_form_data(self.task, title="New title"))  # same
        self.assertEqual(Notification.objects.count(), 0)

    def test_the_rule_itself_refuses_self_assignment(self):
        from projects.notifications import notify_assigned
        self.assertIsNone(notify_assigned(self.task, assigned_by=self.sara))  # the task is Sara's
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_failing_email_still_leaves_the_notification(self):
        from unittest import mock
        self.login(self.omar)
        with mock.patch("projects.views.email_new_assignee", return_value=False):
            self.create_task(self.lina)
        self.assertEqual(self.notes(self.lina).count(), 1)

    # ---- the bell and the list

    def test_the_bell_counts_unread_notifications(self):
        Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar,
                                    read_at=timezone.now())
        self.login(self.sara)
        response = self.client.get("/my-tasks/")
        self.assertEqual(response.context["unread_notifications"], 1)
        self.assertContains(response, 'href="/notifications/"')
        self.login(self.omar)
        self.assertEqual(self.client.get("/my-tasks/").context["unread_notifications"], 0)

    def test_the_list_says_who_gave_which_task(self):
        Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        self.login(self.sara)
        self.assertContains(self.client.get("/notifications/"),
                            "<bdi>Omar Ali</bdi> assigned you the task “<bdi>Install the firewall</bdi>”.")

    def test_the_list_in_french_and_without_the_actor(self):
        Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=None)
        self.sara.language = "fr"
        self.sara.save()
        self.login(self.sara)
        page = self.client.get("/notifications/")
        self.assertContains(page, "La tâche « <bdi>Install the firewall</bdi> » vous a été assignée.")

    def test_an_empty_list(self):
        self.login(self.sara)
        self.assertContains(self.client.get("/notifications/"), "No notifications yet")

    # ---- reading

    def test_opening_marks_as_read_and_goes_to_the_task(self):
        note = Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        self.login(self.sara)
        response = self.client.get(f"/notifications/{note.pk}/")
        self.assertRedirects(response, f"/tasks/{self.task.pk}/")
        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)

    def test_mark_all_as_read_only_touches_my_own(self):
        mine = Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        theirs = Notification.objects.create(recipient=self.lina, kind="assigned", task=self.task, actor=self.omar)
        self.login(self.sara)
        self.assertRedirects(self.client.post("/notifications/read-all/"), "/notifications/")
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertIsNotNone(mine.read_at)
        self.assertIsNone(theirs.read_at)

    def test_mark_all_needs_a_post(self):
        self.login(self.sara)
        self.assertEqual(self.client.get("/notifications/read-all/").status_code, 405)

    # ---- privacy

    def test_other_peoples_notifications_do_not_exist(self):
        note = Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        for person in (self.omar, self.lina, self.karim, self.amina):  # even the admin
            with self.subTest(person=person.username):
                self.login(person)
                self.assertEqual(self.client.get(f"/notifications/{note.pk}/").status_code, 404)
                self.assertNotContains(self.client.get("/notifications/"), "Install the firewall")
        note.refresh_from_db()
        self.assertIsNone(note.read_at)

    def test_someone_removed_from_the_team_no_longer_sees_it(self):
        note = Notification.objects.create(recipient=self.lina, kind="assigned", task=self.task, actor=self.omar)
        ProjectMember.objects.filter(user=self.lina).delete()
        self.login(self.lina)
        self.assertEqual(self.client.get("/my-tasks/").context["unread_notifications"], 0)
        self.assertEqual(self.client.get(f"/notifications/{note.pk}/").status_code, 404)

    def test_deleting_the_task_or_the_actor(self):
        Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.lina)
        self.lina.delete()  # the notification stays, without a name
        self.assertEqual(Notification.objects.get().actor, None)
        Task.objects.filter(pk=self.task.pk).delete()
        self.assertEqual(Notification.objects.count(), 0)

    def test_only_the_latest_are_listed(self):
        for _ in range(60):
            Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        self.login(self.sara)
        self.assertEqual(len(self.client.get("/notifications/").context["notifications"]), 50)
        self.assertEqual(self.client.get("/my-tasks/").context["unread_notifications"], 60)

    def test_a_login_is_needed(self):
        for address in ("/notifications/", "/notifications/1/"):
            self.assertRedirects(self.client.get(address), f"/login/?next={address}", fetch_redirect_response=False)
        self.assertEqual(self.client.post("/notifications/read-all/").status_code, 302)

    def test_old_notifications_keep_their_order(self):
        old = Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        Notification.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        new = Notification.objects.create(recipient=self.sara, kind="assigned", task=self.task, actor=self.omar)
        self.login(self.sara)
        listed = self.client.get("/notifications/").context["notifications"]
        self.assertEqual([n.pk for n in listed], [new.pk, old.pk])
