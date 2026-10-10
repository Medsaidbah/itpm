from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from simple_history.admin import SimpleHistoryAdmin

from .models import Attachment, Comment, Project, ProjectMember, Task


def join_team(task, added_by):
    """In the admin panel, admins can give a task to anyone: that person joins the project's team."""
    if task.assignee_id:
        task.project.add_to_team(task.assignee, added_by=added_by)


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

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            # Remember who added each new team member
            if isinstance(obj, ProjectMember) and obj.pk is None:
                obj.added_by = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()

        # Tasks given to someone outside the team (in the Tasks list): that person joins the team
        if formset.model is Task:
            for task in formset.new_objects:
                join_team(task, added_by=request.user)
            for task, changed_fields in formset.changed_objects:
                if "assignee" in changed_fields:
                    join_team(task, added_by=request.user)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)  # the team and the tasks below
        # A new manager: the previous one stays in the team, as a member
        if change and "manager" in form.changed_data:
            previous_manager = get_user_model().objects.get(pk=form.initial["manager"])
            form.instance.add_to_team(previous_manager, added_by=request.user)


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
    readonly_fields = ["completed_at", "created_at", "updated_at"]
    inlines = [CommentInline, AttachmentInline]

    @admin.display(boolean=True, description="Overdue")
    def overdue(self, obj):
        return obj.is_overdue

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Given to someone outside the team, or moved to another project: the person joins the team
        if not change or "assignee" in form.changed_data or "project" in form.changed_data:
            join_team(obj, added_by=request.user)

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
