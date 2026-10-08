from django import forms

from .models import Project


class BootstrapFormMixin:
    """Gives every field the CSS classes the Tabler design expects.
    Any form that lists this mixin first gets the same look."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
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
            if name in self.fields:
                attrs = self.fields[name].widget.attrs
                if "is-invalid" not in attrs["class"]:
                    attrs["class"] += " is-invalid"
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
