from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from simple_history.admin import SimpleHistoryAdmin

from .models import Attachment, Comment, Project, ProjectMember, Task


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ["title", "status", "assignee", "start_date", "due_date"]
    show_change_link = True


class TeamMemberAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A line already saved can be deleted, not changed into another person:
        # that would take someone out of the team without the checks below
        if self.instance.pk:
            self.fields["user"].disabled = True


class TeamFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        # The same rule as on the project page: someone with open tasks in the project
        # stays in the team until these tasks are given to someone else (or finished)
        for form in self.forms:
            if not (self.can_delete and self._should_delete_form(form) and form.instance.pk):
                continue
            person = form.instance.user
            open_count = self.instance.open_tasks_of(person).count()
            if open_count:
                raise ValidationError(
                    f"{person} still has {open_count} open task(s) in this project. "
                    f"Give them to someone else (or finish them) before removing {person} from the team."
                )


class ProjectMemberInline(admin.TabularInline):
    """The project's team, below the project in the admin panel. The manager is chosen above
    (Manager field) and is always part of the team, so there's no need to add them here."""

    model = ProjectMember
    form = TeamMemberAdminForm
    formset = TeamFormSet
    fk_name = "project"
    extra = 0
    fields = ["user", "added_at", "added_by"]
    readonly_fields = ["added_at", "added_by"]
    verbose_name = "team member"
    verbose_name_plural = "Team (besides the manager)"


@admin.register(Project)
class ProjectAdmin(SimpleHistoryAdmin):
    list_display = [
        "name", "status", "priority", "manager", "start_date", "end_date", "progress_display",
    ]
    list_filter = ["status", "priority", "manager"]
    search_fields = ["name", "description"]
    inlines = [ProjectMemberInline, TaskInline]

    @admin.display(description="Progress")
    def progress_display(self, obj):
        return f"{obj.progress}%"

    def save_model(self, request, obj, form, change):
        # A new manager: saving keeps the previous one in the team, remembered as added by
        # this admin (see Project.save). This also works for "Revert" on the History page.
        obj.saved_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, ProjectMember) and obj.pk is None:
                # Already in the team: the previous manager, kept in the team just before
                if ProjectMember.objects.filter(project=obj.project, user=obj.user).exists():
                    continue
                obj.added_by = request.user  # remember who added each new team member
            # A task given to someone outside the team: saving it puts that person in the team,
            # and they are remembered as added by this admin (see Task.save)
            if isinstance(obj, Task):
                obj.saved_by = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


class TaskAdminForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        # Like the task form of the app: a task only waits for tasks of its own project.
        # A link to another project would show that task's title to people outside its project.
        project = cleaned_data.get("project")
        project_id = project.pk if project else self.instance.project_id
        waits_for = cleaned_data.get("depends_on") or []
        if project_id and any(task.project_id != project_id for task in waits_for):
            self.add_error("depends_on", "A task can only wait for tasks of its own project.")
        return cleaned_data


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    fields = ["body", "author", "created_at"]
    readonly_fields = ["author", "created_at"]


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    fields = ["file", "uploaded_by", "uploaded_at"]
    readonly_fields = ["uploaded_by", "uploaded_at"]


@admin.register(Task)
class TaskAdmin(SimpleHistoryAdmin):
    list_display = [
        "title", "project", "status", "priority", "assignee", "due_date", "is_milestone", "overdue",
    ]
    list_filter = ["project", "status", "priority", "assignee", "is_milestone"]
    search_fields = ["title", "description"]
    filter_horizontal = ["depends_on"]
    form = TaskAdminForm
    readonly_fields = ["completed_at", "created_at", "updated_at"]
    inlines = [CommentInline, AttachmentInline]

    @admin.display(boolean=True, description="Overdue")
    def overdue(self, obj):
        return obj.is_overdue

    def get_readonly_fields(self, request, obj=None):
        # A task stays in the project it was created in. Moving it would leave its subtasks,
        # its links and its history in the old project, where its team could still read them.
        fields = list(super().get_readonly_fields(request, obj))
        return fields + ["project"] if obj else fields

    def save_model(self, request, obj, form, change):
        # Given to someone outside the team: saving the task puts that person in the team,
        # remembered as added by this admin (see Task.save)
        obj.saved_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        # Fill in the comment author and the file uploader automatically
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, Comment) and obj.author_id is None:
                obj.author = request.user
            if isinstance(obj, Attachment) and obj.uploaded_by_id is None:
                obj.uploaded_by = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()
