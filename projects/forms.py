from django import forms

from .models import Project, Task


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
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            # type="date" shows the browser's date picker, which needs dates as year-month-day
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


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
            "assignee": "Assigned to",
            "parent": "Main task",
            "is_milestone": "This task is a milestone",
            "depends_on": "Waits for",
        }
        help_texts = {
            "parent": "Pick a main task to make this a subtask of it.",
            "depends_on": "Tick the tasks that must be done before this one can start.",
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        # The project comes from the page address, not from the form
        self.instance.project = project

        # Only this project's tasks can be chosen, and never the task itself
        others = project.tasks.select_related("parent")
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)

        self.fields["parent"].queryset = others.filter(parent__isnull=True)
        self.fields["parent"].empty_label = "None (this is a main task)"
        self.fields["assignee"].empty_label = "Unassigned"
        self.fields["depends_on"].queryset = others
        # Show subtasks as "Main task › Subtask", so tasks with the same name can be told apart
        self.fields["depends_on"].label_from_instance = (
            lambda task: f"{task.parent} › {task}" if task.parent_id else str(task)
        )

    def clean_depends_on(self):
        chosen = self.cleaned_data["depends_on"]
        if self.instance.pk:
            # Refuse loops, like "A waits for B" while "B waits for A"
            for task in chosen:
                if task.waits_for(self.instance):
                    raise forms.ValidationError(
                        f'"{task}" already waits for this task, so this task can\'t wait for it.'
                    )
        return chosen
