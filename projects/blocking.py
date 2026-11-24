"""A task that becomes Blocked needs a reason (release 1.1, see docs/ROADMAP.md).

Every way a task can become Blocked checks it on the server, with the helpers below: the status buttons of the
task's page, the board, the task form (new task and edit), and the admin panel (the task's page, its "Revert"
from the history, and the tasks table of a project's page). A request without a reason, or with only spaces,
is refused and changes nothing.

The reason is saved as a comment on the task, by the person who blocked it, starting with Comment.BLOCKING_MARKER.
The marker is stored the same in every language; the pages show "Reason for blocking" in the reader's language.
Only the change to Blocked needs a reason: a task that is already Blocked (saved again, or moved inside the
Blocked column) and unblocking need none."""

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import Comment, Task

REASON_FIELD = "blocked_reason"  # the name of the reason in every form and request
EXPECTED_END_FIELD = "blocked_expected_end"  # release 1.5: when the block should end (optional)


def becomes_blocked(task, new_status):
    """True when this status makes the task Blocked and it isn't Blocked yet. "Yet" is read from the database:
    the copy of the task a form works on may already hold the new status (the admin panel's "Revert" does that)."""
    if new_status != Task.Status.BLOCKED:
        return False
    if task.pk is None:  # a new task
        return True
    return Task.objects.filter(pk=task.pk).values_list("status", flat=True).first() != Task.Status.BLOCKED


def clean_reason(text):
    """The reason without the spaces around it ("" when there are only spaces)."""
    return (text or "").strip()


def missing_reason_message():
    return _("Say why the task is blocked. The reason is added to the task's comments.")


def record_reason(task, author, reason):
    """The comment that says why the task was blocked, written by the person who blocked it."""
    return Comment.objects.create(task=task, author=author, body=Comment.BLOCKING_MARKER + reason)


class BlockingReasonForm(forms.ModelForm):
    """For every form that can change a task's status: a "Reason for blocking" field, required when the status
    becomes Blocked. After saving, the view or the admin panel calls record_reason() with form.blocking_reason."""

    blocked_reason = forms.CharField(
        label=gettext_lazy("Reason for blocking"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=gettext_lazy("Needed when the task becomes Blocked. It is added to the task's comments, with your name."),
    )
    # Release 1.5: when the block should end, optional (it goes into the block's record, projects/status.py)
    blocked_expected_end = forms.DateField(
        label=gettext_lazy("Expected end"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text=gettext_lazy("Optional: when the task should be able to go on."),
    )
    blocking_reason = None  # filled in by clean() when the task becomes Blocked
    blocking_expected_end = None

    def clean(self):
        cleaned_data = super().clean()
        self.blocking_reason = self.blocking_expected_end = None
        if becomes_blocked(self.instance, cleaned_data.get("status")):
            reason = clean_reason(cleaned_data.get(REASON_FIELD))
            if reason:
                self.blocking_reason = reason
                self.blocking_expected_end = cleaned_data.get("blocked_expected_end")
            else:
                self.add_error(REASON_FIELD, missing_reason_message())
        return cleaned_data
