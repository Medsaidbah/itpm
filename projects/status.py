"""Every change of a task's status goes through change_status() (release 1.5, a refactor: nothing behaves
differently). The paths that change a status, each keeping its own permission check:

  1. the status buttons of the task's page (also the blocking dialog, and "Done" on My tasks): views.task_status
  2. the board's drag and drop: views.task_move
  3. the new task form: views.task_create
  4. the edit task form: views.task_edit
  5. the admin panel, a task's page: TaskAdmin.save_model
  6. the admin panel, "Revert" from a task's history (simple_history calls the same save_model)
  7. the admin panel, the tasks table of a project's page: ProjectAdmin.save_formset

What happens to every change, here only: the blocking reason rule (projects/blocking.py: a task that becomes
Blocked needs a reason, saved as a comment by the person), the block's record (release 1.5: TaskBlock, opened when
a task becomes Blocked and closed when it leaves Blocked; written nowhere else), completed_at (written by
Task.save), the person recorded in the history, and, where the path asked for it, the "unblocked" notification and
email to the people whose task was waiting for this one. projects/tests/test_status.py fails if any other code sets a task's status itself.
"""

from datetime import date

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .blocking import clean_reason, missing_reason_message, record_reason
from .models import Task, TaskBlock
from .notifications import tell_unblocked


class StatusRefused(Exception):
    """The change was refused (nothing was saved). `message` says why, in the language of the page."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def status_in_database(task):
    """The status the task has now in the database (None for a new task). The copy of the task may already hold
    the new status: a form puts it there, and so does the admin panel's "Revert"."""
    if task.pk is None:
        return None
    return Task.objects.filter(pk=task.pk).values_list("status", flat=True).first()


def read_expected_end(text):
    """The expected end sent by the blocking dialog ("2026-12-15"), as a date; None when there is none.
    Raises StatusRefused when it isn't a date."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise StatusRefused(_("The expected end isn't a valid date.")) from None


def take_new_status(task):
    """For the paths whose form has already put the new status on the task: returns that new status, and puts back
    the one the task has in the database, so that change_status() is the only one that changes it."""
    new_status = task.status
    old_status = status_in_database(task)
    if old_status is not None:
        task.status = old_status
    return new_status


def change_status(task, new_status, by, reason="", request=None, notify=False, warn=True, save_related=None,
                  after_save=None, expected_end=None):
    """Gives the task this status and saves it, with everything a status change brings (see the top of this file).
    The whole task is saved once: the other fields a form changed are in the same save (one version in the history).

    by: the person making the change. reason: why it becomes Blocked (required then; ignored otherwise).
    expected_end: when the block should end (a date, optional; only when it becomes Blocked).
    notify: tell the people whose task was waiting for this one when it becomes Done (needs the request).
    warn: say on the next page when such an email fails (the board doesn't). save_related: a form's save_m2m, done
    in the same transaction right after the task is saved. after_save: called once the change is saved, before the
    notifications, so a page's own messages and emails come in the same order as before.
    Raises StatusRefused, and saves nothing, when the rule refuses the change."""
    old_status = status_in_database(task)
    if new_status not in Task.Status.values:
        raise StatusRefused(_("Unknown status."))
    reason = clean_reason(reason)
    becomes_blocked = new_status == Task.Status.BLOCKED and old_status != Task.Status.BLOCKED
    if becomes_blocked and not reason:
        raise StatusRefused(missing_reason_message())

    with transaction.atomic():  # the status and the reason it is blocked are saved together, or not at all
        task.status = new_status
        task.saved_by = by  # for Task.save (who put a new person in the team)
        task._history_user = by  # the person in the history (the same as the request's)
        task._status_by_change_status = True  # lets projects/tests/test_status.py check that every path comes here
        try:
            task.save()  # writes completed_at too
        finally:
            task._status_by_change_status = False
        if save_related:
            save_related()
        if becomes_blocked:
            record_reason(task, by, reason)  # the comment, as since release 1.1
            # Release 1.5: the block's record, open until the task leaves Blocked
            TaskBlock.objects.create(task=task, reason=reason, started_by=by, expected_end=expected_end)
        elif old_status == Task.Status.BLOCKED and new_status != Task.Status.BLOCKED:
            # Leaves Blocked: its open record ends (a task blocked before release 1.5 has none: nothing to close)
            TaskBlock.objects.filter(task=task, ended_at__isnull=True).update(ended_at=timezone.now(), ended_by=by)
    if after_save:
        after_save()
    if notify and new_status == Task.Status.DONE and old_status != Task.Status.DONE:
        tell_unblocked(request, task, warn=warn)
    return old_status != new_status
