from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from .models import Attachment, Comment, Project, Task

# The largest file people can upload, in megabytes
MAX_UPLOAD_MB = 10


class BootstrapFormMixin:
    """Gives every field the CSS classes the Tabler design expects.
    Any form that lists this mixin first gets the same look."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                css_class = "form-check-input"
            elif isinstance(widget, forms.Select):
                css_class = "form-select"
            else:
                css_class = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css_class}".strip()

    def is_valid(self):
        valid = super().is_valid()
        # Fields with an error get a red border
        for name in self.errors:
            if name not in self.fields:
                continue  # an error about the whole form, not one field
            widget = self.fields[name].widget
            if isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue  # lists of tick boxes get a red frame in the template instead
            if "is-invalid" not in widget.attrs["class"]:
                widget.attrs["class"] += " is-invalid"
        return valid


class ProjectForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "status", "priority", "manager", "start_date", "end_date"]
        labels = {
            "name": _("Name"),
            "description": _("Description"),
            "status": _("Status"),
            "priority": _("Priority"),
            "manager": _("Manager"),
            "start_date": _("Start date"),
            "end_date": _("End date"),
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            # type="date" shows the browser's date picker, which needs dates as year-month-day
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user, **kwargs):
        """user: the person filling in the form."""
        super().__init__(*args, **kwargs)
        manager = self.fields["manager"]
        manager.queryset = manager.queryset.order_by("first_name", "last_name", "username")
        if not self.instance.can_change_manager(user):
            # Shown, but it can't be changed (Django ignores any other value sent for it).
            # So the list only holds the current manager: there is nothing else to choose.
            manager.disabled = True
            manager.queryset = manager.queryset.filter(pk=self.instance.manager_id)
            manager.help_text = _("Only admins can change the manager.")
        else:
            manager.help_text = _("The manager can edit the project, its tasks and its team.")


class ProjectDeleteForm(BootstrapFormMixin, forms.Form):
    """Deleting a project can't be undone, so its name must be typed again to confirm."""

    name = forms.CharField(
        label=_("Type the project's name to confirm"),
        max_length=200,
        error_messages={"required": _("Type the project's name to confirm.")},
    )

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["name"].widget.attrs["autocomplete"] = "off"

    def clean_name(self):
        # The spaces before and after are left out (by Django); everything else must be the same
        if self.cleaned_data["name"] != self.project.name.strip():
            raise forms.ValidationError(_("This isn't the project's name. Type it exactly as it is written above."))
        return self.cleaned_data["name"]


class TaskForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            "title", "description", "status", "priority", "assignee",
            "start_date", "due_date", "parent", "is_milestone", "depends_on",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            # A list of tick boxes instead of a list where you need Ctrl+click
            "depends_on": forms.CheckboxSelectMultiple,
        }
        labels = {
            "title": _("Title"),
            "description": _("Description"),
            "status": _("Status"),
            "priority": _("Priority"),
            "assignee": _("Assigned to"),
            "start_date": _("Start date"),
            "due_date": _("Due date"),
            "parent": _("Main task"),
            "is_milestone": _("This task is a milestone"),
            "depends_on": _("Waits for"),
        }
        help_texts = {
            "parent": _("Pick a main task to make this a subtask of it."),
            "depends_on": _("Tick the tasks that must be done before this one can start."),
        }

    def __init__(self, *args, project, user, **kwargs):
        """project: the task's project. user: the person filling in the form."""
        super().__init__(*args, **kwargs)
        # The project comes from the page address, not from the form
        self.instance.project = project

        # Only this project's tasks can be chosen, and never the task itself
        others = project.tasks.select_related("parent")
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)

        self.fields["parent"].queryset = others.filter(parent__isnull=True)
        self.fields["parent"].empty_label = _("None (this is a main task)")
        self.fields["depends_on"].queryset = others
        # Show subtasks as "Main task › Subtask", so tasks with the same name can be told apart
        self.fields["depends_on"].label_from_instance = (
            lambda task: f"{task.parent} › {task}" if task.parent_id else str(task)
        )
        self.set_assignee_choices(project, user)

    def set_assignee_choices(self, project, user):
        """Who the task can be given to (only people whose account is active):
        - the people in the project's team;
        - the person the task is assigned to now, even if they left the team,
          so that saving the task never takes it away from them;
        - admins can choose anyone: a person outside the team joins it when the task is saved."""
        field = self.fields["assignee"]
        field.empty_label = _("Unassigned")
        people = get_user_model().objects.filter(is_active=True).order_by("first_name", "last_name", "username")

        if not user.is_admin:
            allowed = Q(pk__in=project.team_queryset().values("pk"))
            if self.instance.assignee_id:
                allowed |= Q(pk=self.instance.assignee_id)
            field.queryset = people.filter(allowed)
            field.help_text = _(
                "Only people in this project's team are listed. People are added to the team on the project page."
            )
            field.error_messages["invalid_choice"] = _("Choose someone in the project's team.")
            return

        # Admins: everyone, in two groups in the list: the team first, then the others
        field.queryset = people
        team = set(project.team_queryset().values_list("pk", flat=True))
        in_team = [(person.pk, str(person)) for person in people if person.pk in team]
        others = [(person.pk, str(person)) for person in people if person.pk not in team]
        groups = [(_("In the team"), in_team), (_("Not in the team: they will join it"), others)]
        field.choices = [("", field.empty_label)] + [(label, options) for label, options in groups if options]
        field.help_text = _(
            "As an admin, you can choose anyone. A person outside the team joins the team when you save."
        )

    def clean_depends_on(self):
        chosen = self.cleaned_data["depends_on"]
        if self.instance.pk:
            # Refuse loops, like "A waits for B" while "B waits for A"
            for task in chosen:
                if task.waits_for(self.instance):
                    raise forms.ValidationError(
                        gettext('"%(task)s" already waits for this task, so this task can\'t wait for it.')
                        % {"task": task}
                    )
        return chosen


class TeamMemberForm(BootstrapFormMixin, forms.Form):
    """The "Add to the team" box on the project page."""

    person = forms.ModelChoiceField(
        queryset=None,  # set below, for each project
        empty_label=_("Choose a person…"),
        error_messages={
            "required": _("Choose a person to add to the team."),
            "invalid_choice": _(
                "This person can't be added: they are in the team already, or their account is deactivated."
            ),
        },
    )

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        # People whose account is active and who aren't in the team yet
        self.fields["person"].queryset = (
            get_user_model().objects.filter(is_active=True)
            .exclude(pk__in=project.team_queryset().values("pk"))
            .order_by("first_name", "last_name", "username")
        )


class CommentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "placeholder": _("Write a comment…")})}
        labels = {"body": _("Comment")}


class AttachmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ["file"]
        labels = {"file": _("File")}
        error_messages = {"file": {"required": _("Choose a file before clicking Upload.")}}

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(
                gettext("This file is too big: files can be up to %(size)s MB.") % {"size": MAX_UPLOAD_MB}
            )
        return file
