"""A task that becomes Blocked needs a reason, on every path (release 1.1). The rules are written at the top of
projects/blocking.py. The reason is a comment on the task, by the person who blocked it."""

from html.parser import HTMLParser

from django.core import mail

from accounts.models import User
from projects.models import Comment, Notification, Task

from .base import TeamTestCase

MISSING = "Say why the task is blocked. The reason is added to the task&#x27;s comments."
MISSING_TEXT = "Say why the task is blocked. The reason is added to the task's comments."


class FormReader(HTMLParser):
    """The fields of the first form of a page that has an "id" (the admin panel's change form), as they would
    be sent: hidden fields, text, dates, ticked boxes, chosen options and text areas."""

    def __init__(self):
        super().__init__()
        self.data, self.inside, self.select, self.textarea = {}, False, None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id", "").endswith("_form"):
            self.inside = True
        if not self.inside:
            return
        name = attrs.get("name")
        if tag == "input" and name and attrs.get("type") not in ("submit", "button", "file"):
            if attrs.get("type") == "checkbox":
                if "checked" in attrs:
                    self.data.setdefault(name, []).append(attrs.get("value", "on"))
            else:
                self.data[name] = attrs.get("value", "")
        elif tag == "select" and name:
            self.select = name
            self.data.setdefault(name, [])
        elif tag == "option" and self.select and "selected" in attrs:
            self.data[self.select].append(attrs.get("value", ""))
        elif tag == "textarea" and name:
            self.textarea = name
            self.data[name] = ""

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "select":
            self.select = None
        elif tag == "textarea":
            self.textarea = None

    def handle_data(self, data):
        if self.textarea:
            self.data[self.textarea] += data


def form_data(response, **changes):
    reader = FormReader()
    reader.feed(response.content.decode())
    reader.data.update(changes)
    return reader.data


class BlockingTestCase(TeamTestCase):
    def setUp(self):
        self.login(self.sara)  # the task is hers: she may change its status

    def reload(self):
        self.task.refresh_from_db()
        return self.task

    def reasons(self, task=None):
        return [(c.author, c.blocking_reason) for c in Comment.objects.filter(task=task or self.task)]

    def assert_nothing_changed(self, status="todo"):
        self.assertEqual(self.reload().status, status)
        self.assertEqual(Comment.objects.count(), 0)


class StatusButtonTests(BlockingTestCase):
    """The status buttons of the task's page (POST /tasks/<id>/status/)."""

    def block(self, **data):
        return self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "blocked", **data}, follow=True)

    def test_without_a_reason_nothing_changes(self):
        response = self.block()
        self.assertContains(response, MISSING)
        self.assert_nothing_changed()

    def test_a_reason_of_spaces_is_no_reason(self):
        self.assertContains(self.block(blocked_reason="   \n\t "), MISSING)
        self.assert_nothing_changed()

    def test_with_a_reason(self):
        response = self.block(blocked_reason="  The supplier hasn't delivered the rack.  ")
        self.assertContains(response, "&quot;Install the firewall&quot; is now Blocked.")
        self.assertEqual(self.reload().status, "blocked")
        self.assertEqual(self.reasons(), [(self.sara, "The supplier hasn't delivered the rack.")])
        self.assertEqual(Comment.objects.get().body, "[blocked] The supplier hasn't delivered the rack.")

    def test_a_task_already_blocked_needs_no_new_reason(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")  # blocked before release 1.1: no reason
        self.block()
        self.assert_nothing_changed(status="blocked")

    def test_unblocking_needs_no_reason(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "in_progress"})
        self.assert_nothing_changed(status="in_progress")

    def test_other_statuses_are_unchanged(self):
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "done"})
        self.assert_nothing_changed(status="done")

    def test_only_the_people_who_may_change_the_status(self):
        self.login(self.karim)  # outside the project
        self.assertEqual(self.block(blocked_reason="Because").status_code, 404)
        self.login(self.omar)  # the project's manager: allowed, and the comment is his
        self.block(blocked_reason="Waiting for the budget")
        self.assertEqual(self.reasons(), [(self.omar, "Waiting for the budget")])

    def test_the_page_opens_the_dialog_instead_of_sending(self):
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, '<button type="button" class="btn" data-bs-toggle="modal" '
                                      'data-bs-target="#block-dialog">Blocked</button>', html=True)
        self.assertNotContains(response, 'name="status" value="blocked" class="btn"')
        self.assertContains(response, f'action="/tasks/{self.task.pk}/status/" id="block-form"')
        self.assertContains(response, '<textarea class="form-control" id="block-reason" name="blocked_reason"')


class BoardTests(BlockingTestCase):
    """Dragging a card on the board (POST /tasks/<id>/move/, answered in JSON)."""

    def move(self, status="blocked", order=None, **data):
        return self.client.post(f"/tasks/{self.task.pk}/move/",
                                {"status": status, "order": order or [self.task.pk], **data})

    def test_without_a_reason_the_move_is_refused_and_nothing_changes(self):
        other = Task.objects.create(project=self.project, title="Other", status="blocked", position=0)
        Task.objects.filter(pk=self.task.pk).update(position=5)
        response = self.move(order=[self.task.pk, other.pk])  # what a card dropped on top of "Other" sends
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": MISSING_TEXT})
        self.assertEqual(self.reload().status, "todo")
        # Not even the order of the cards: the board puts the card back where it was
        self.assertEqual((self.task.position, Task.objects.get(pk=other.pk).position), (5, 0))
        self.assertEqual(Comment.objects.count(), 0)

    def test_a_reason_of_spaces_is_refused(self):
        self.assertEqual(self.move(blocked_reason="  ").status_code, 400)
        self.assert_nothing_changed()

    def test_with_a_reason(self):
        response = self.move(blocked_reason="Waiting for the licence")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reload().status, "blocked")
        self.assertEqual(self.reasons(), [(self.sara, "Waiting for the licence")])

    def test_moving_inside_the_blocked_column_needs_no_reason(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")
        self.assertEqual(self.move().status_code, 200)
        self.assert_nothing_changed(status="blocked")

    def test_moving_out_of_blocked_needs_no_reason(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")
        self.assertEqual(self.move(status="in_progress").status_code, 200)
        self.assert_nothing_changed(status="in_progress")

    def test_cancelling_sends_nothing_so_nothing_changes(self):
        # Cancel is handled in the browser: the card goes back and no request is sent. The server's side of it:
        # the board page only, then no move. The task is exactly as before.
        self.client.get(f"/projects/{self.project.pk}/board/")
        self.assert_nothing_changed()

    def test_the_board_has_the_dialog(self):
        response = self.client.get(f"/projects/{self.project.pk}/board/")
        self.assertContains(response, 'id="block-dialog"')
        self.assertContains(response, '<form class="modal-content" method="post"  id="block-form" novalidate')
        self.assertContains(response, "tabler.Modal.getOrCreateInstance")

    def test_someone_who_may_not_move_the_card_gets_nothing(self):
        lina = User.objects.create_user("lina", first_name="Lina")
        self.project.add_to_team(lina)
        self.login(lina)  # in the team, but the task isn't hers
        self.assertEqual(self.move(blocked_reason="Because").status_code, 403)
        self.assert_nothing_changed()


class TaskFormTests(BlockingTestCase):
    """The task form: editing a task, and creating one directly as Blocked."""

    def setUp(self):
        self.login(self.omar)  # the manager edits and creates tasks

    def edit(self, **changes):
        data = self.task_form_data(**changes)
        return self.client.post(f"/tasks/{self.task.pk}/edit/", data)

    def test_without_a_reason_the_form_asks_for_it(self):
        response = self.edit(status="blocked")
        self.assertEqual(response.status_code, 200)  # the form again, with the message under the field
        self.assertContains(response, MISSING)
        self.assertFormError(response.context["form"], "blocked_reason", MISSING_TEXT)
        self.assert_nothing_changed()

    def test_a_reason_of_spaces_is_no_reason(self):
        self.assertContains(self.edit(status="blocked", blocked_reason="    "), MISSING)
        self.assert_nothing_changed()

    def test_with_a_reason(self):
        self.edit(status="blocked", blocked_reason="Waiting for Sara's test results")
        self.assertEqual(self.reload().status, "blocked")
        self.assertEqual(self.reasons(), [(self.omar, "Waiting for Sara's test results")])

    def test_a_blocked_task_edited_without_changing_its_status(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")
        self.edit(status="blocked", title="Install the new firewall")
        self.assertEqual(self.reload().title, "Install the new firewall")
        self.assert_nothing_changed(status="blocked")

    def test_a_reason_without_blocking_is_ignored(self):
        self.edit(status="in_progress", blocked_reason="Not needed")
        self.assert_nothing_changed(status="in_progress")

    def test_creating_a_blocked_task(self):
        data = self.task_form_data(title="New one", status="blocked")
        response = self.client.post(f"/projects/{self.project.pk}/tasks/new/", data)
        self.assertContains(response, MISSING)
        self.assertFalse(Task.objects.filter(title="New one").exists())

        data["blocked_reason"] = "The room isn't ready"
        self.client.post(f"/projects/{self.project.pk}/tasks/new/", data)
        new = Task.objects.get(title="New one")
        self.assertEqual((new.status, self.reasons(new)), ("blocked", [(self.omar, "The room isn't ready")]))

    def test_the_form_knows_the_status_before(self):
        Task.objects.filter(pk=self.task.pk).update(status="blocked")
        response = self.client.get(f"/tasks/{self.task.pk}/edit/")
        self.assertContains(response, '<div id="blocked-reason-field" data-status-before="blocked">')
        self.assertContains(response, 'name="blocked_reason"')


class AdminPanelTests(BlockingTestCase):
    """Django's admin panel (a technical tool, for admins with staff access): the task's page, its "Revert"
    from the history, and the tasks table of a project's page."""

    def setUp(self):
        self.login(self.amina)

    def test_the_tasks_page(self):
        url = f"/admin/projects/task/{self.task.pk}/change/"
        response = self.client.post(url, form_data(self.client.get(url), status=["blocked"]))
        self.assertContains(response, MISSING)
        self.assert_nothing_changed()

        response = self.client.post(url, form_data(self.client.get(url), status=["blocked"],
                                                   blocked_reason="Waiting for the vendor"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.reload().status, "blocked")
        self.assertEqual(self.reasons(), [(self.amina, "Waiting for the vendor")])

    def test_revert_from_the_history(self):
        # A version where the task was Blocked (before release 1.1, without a reason), then unblocked
        Task.objects.filter(pk=self.task.pk).update(status="blocked")
        task = self.reload()
        task.save()  # a version with "blocked"
        old = task.history.latest()
        task.status = "todo"
        task.save()
        url = f"/admin/projects/task/{task.pk}/history/{old.history_id}/"
        response = self.client.post(url, form_data(self.client.get(url)))
        self.assertContains(response, MISSING)
        self.assert_nothing_changed()

        self.client.post(url, form_data(self.client.get(url), blocked_reason="Back to blocked: still no rack"))
        self.assertEqual(self.reload().status, "blocked")
        self.assertEqual(self.reasons(), [(self.amina, "Back to blocked: still no rack")])

    def test_the_tasks_table_of_a_projects_page(self):
        url = f"/admin/projects/project/{self.project.pk}/change/"
        data = form_data(self.client.get(url), **{"tasks-0-status": ["blocked"]})
        self.assertEqual(data["tasks-0-id"], str(self.task.pk))
        response = self.client.post(url, data)
        self.assertContains(response, MISSING)
        self.assert_nothing_changed()

        data["tasks-0-blocked_reason"] = "Waiting for the electrician"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.reload().status, "blocked")
        self.assertEqual(self.reasons(), [(self.amina, "Waiting for the electrician")])


class ReasonOnThePageTests(BlockingTestCase):
    def block(self):
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "blocked", "blocked_reason": "No rack yet"})

    def test_the_reason_appears_with_its_author(self):
        self.block()
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, '<span class="badge bg-red-lt me-1"><i class="ti ti-hand-stop me-1"></i>'
                                      "Reason for blocking</span> No rack yet")
        self.assertContains(response, f'<a href="/people/{self.sara.pk}/" class="text-reset">Sara Ben</a>')
        self.assertNotContains(response, "[blocked]")  # the marker itself is never shown

    def test_the_activity_page(self):
        self.block()
        response = self.client.get(f"/activity/?task={self.task.pk}")
        self.assertContains(response, "gave the reason for blocking")
        self.assertContains(response, "No rack yet")
        self.assertNotContains(response, "[blocked]")

    def test_a_comment_cannot_imitate_a_reason(self):
        response = self.client.post(f"/tasks/{self.task.pk}/comments/add/", {"body": "  [blocked] fake"}, follow=True)
        self.assertContains(response, "the app uses it to mark the reasons for blocking")
        self.assertEqual(Comment.objects.count(), 0)
        self.client.post(f"/tasks/{self.task.pk}/comments/add/", {"body": "Not [blocked] at the start"})
        self.assertEqual(Comment.objects.get().blocking_reason, None)

    def test_an_empty_comment_keeps_its_message(self):
        response = self.client.post(f"/tasks/{self.task.pk}/comments/add/", {"body": "   "}, follow=True)
        self.assertContains(response, "Write something before sending a comment.")

    def test_no_notification_and_no_email_as_before(self):
        mail.outbox = []
        self.login(self.omar)  # the manager blocks Sara's task
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "blocked", "blocked_reason": "Budget"})
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(mail.outbox, [])


class LanguageTests(BlockingTestCase):
    def test_in_french(self):
        self.sara.language = "fr"
        self.sara.save()
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "blocked", "blocked_reason": "Pas de baie"})
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, "Raison du blocage</span> Pas de baie")
        Task.objects.filter(pk=self.task.pk).update(status="todo")
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, "Pourquoi cette tâche est-elle bloquée ?")
        self.assertContains(response, "Bloquer la tâche")
        response = self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "blocked"}, follow=True)
        self.assertContains(response, "Indiquez pourquoi la tâche est bloquée.")

    def test_in_arabic(self):
        self.sara.language = "ar"
        self.sara.save()
        response = self.client.get(self.task.get_absolute_url())
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, "لماذا هذه المهمة متوقفة؟")
        self.assertContains(response, "إيقاف المهمة")
        response = self.client.post(f"/tasks/{self.task.pk}/move/", {"status": "blocked", "order": [self.task.pk]})
        self.assertEqual(response.json(), {"error": "اذكر سبب توقف المهمة. يُضاف السبب إلى تعليقات المهمة."})
        self.client.post(f"/tasks/{self.task.pk}/status/", {"status": "blocked", "blocked_reason": "لا توجد خزانة"})
        self.assertContains(self.client.get(self.task.get_absolute_url()), "سبب التوقف</span> لا توجد خزانة")
