from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from simple_history.admin import SimpleHistoryAdmin

from accounts.models import User, people_who_can_work

from .blocking import BlockingReasonForm
from .status import change_status, take_new_status
from .models import Attachment, Comment, Project, ProjectMember, Task


class NoExecutivesMixin:
    """Release 1.2: the lists of people of the admin panel (a project's manager, its team, a task's person) never
    offer an executive, who reads everything and is given nothing to do. Like everywhere: active accounts only."""

    PEOPLE_FIELDS = {"manager", "assignee", "user"}

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in self.PEOPLE_FIELDS and db_field.related_model is User:
            kwargs["queryset"] = people_who_can_work().order_by("first_name", "last_name", "username")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class TaskInlineForm(BlockingReasonForm):
    """A line of the tasks table of a project's page: like everywhere, a task that becomes Blocked needs a reason."""

    blocked_reason = forms.CharField(label=gettext_lazy("Reason for blocking"), required=False,
                                     widget=forms.TextInput)


class TaskInline(NoExecutivesMixin, admin.TabularInline):
    model = Task
    form = TaskInlineForm
    extra = 0
    fields = ["title", "status", "blocked_reason", "blocked_expected_end", "assignee", "start_date", "due_date"]
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
                    _(
                        "%(person)s still has %(count)d open task(s) in this project. "
                        "Give them to someone else (or finish them) before removing %(person)s from the team."
                    ) % {"person": person, "count": open_count}
                )


class ProjectMemberInline(NoExecutivesMixin, admin.TabularInline):
    """The project's team, below the project in the admin panel. The manager is chosen above
    (Manager field) and is always part of the team, so there's no need to add them here."""

    model = ProjectMember
    form = TeamMemberAdminForm
    formset = TeamFormSet
    fk_name = "project"
    extra = 0
    fields = ["user", "added_at", "added_by"]
    readonly_fields = ["added_at", "added_by"]
    verbose_name = gettext_lazy("team member")
    verbose_name_plural = gettext_lazy("Team (besides the manager)")


@admin.register(Project)
class ProjectAdmin(NoExecutivesMixin, SimpleHistoryAdmin):
    list_display = [
        "name", "status", "priority", "manager", "start_date", "end_date", "progress_display",
    ]
    list_filter = ["status", "priority", "manager"]
    search_fields = ["name", "description"]
    inlines = [ProjectMemberInline, TaskInline]

    @admin.display(description=gettext_lazy("Progress"))
    def progress_display(self, obj):
        return f"{obj.progress}%"

    def save_model(self, request, obj, form, change):
        # A new manager: saving keeps the previous one in the team, remembered as added by
        # this admin (see Project.save). This also works for "Revert" on the History page.
        obj.saved_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        forms_by_instance = {id(line.instance): line for line in formset.forms}
        for obj in instances:
            if isinstance(obj, ProjectMember) and obj.pk is None:
                # Already in the team: the previous manager, kept in the team just before
                if ProjectMember.objects.filter(project=obj.project, user=obj.user).exists():
                    continue
                obj.added_by = request.user  # remember who added each new team member
            # A task of the table (path 7 of projects/status.py): change_status gives its status and saves it, with
            # the reason it was just blocked, as a comment by this admin; a task given to someone outside the team
            # puts that person in the team, remembered as added by this admin (see Task.save). No "unblocked"
            # notification from the admin panel (as before).
            if isinstance(obj, Task):
                task_form = forms_by_instance.get(id(obj))
                reason = getattr(task_form, "blocking_reason", None) or ""
                change_status(obj, take_new_status(obj), by=request.user, reason=reason,
                              expected_end=getattr(task_form, "blocking_expected_end", None))
                continue
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


class TaskAdminForm(BlockingReasonForm):
    def clean(self):
        cleaned_data = super().clean()
        # Like the task form of the app: a task only waits for tasks of its own project.
        # A link to another project would show that task's title to people outside its project.
        project = cleaned_data.get("project")
        project_id = project.pk if project else self.instance.project_id
        waits_for = cleaned_data.get("depends_on") or []
        if project_id and any(task.project_id != project_id for task in waits_for):
            self.add_error("depends_on", _("A task can only wait for tasks of its own project."))
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
class TaskAdmin(NoExecutivesMixin, SimpleHistoryAdmin):
    list_display = [
        "title", "project", "status", "priority", "assignee", "due_date", "is_milestone", "overdue",
    ]
    list_filter = ["project", "status", "priority", "assignee", "is_milestone"]
    search_fields = ["title", "description"]
    filter_horizontal = ["depends_on"]
    form = TaskAdminForm
    readonly_fields = ["completed_at", "created_at", "updated_at"]
    inlines = [CommentInline, AttachmentInline]

    @admin.display(boolean=True, description=gettext_lazy("Overdue"))
    def overdue(self, obj):
        return obj.is_overdue

    def get_readonly_fields(self, request, obj=None):
        # A task stays in the project it was created in. Moving it would leave its subtasks,
        # its links and its history in the old project, where its team could still read them.
        fields = list(super().get_readonly_fields(request, obj))
        return fields + ["project"] if obj else fields

    def save_model(self, request, obj, form, change):
        # A task's page and its "Revert" (paths 5 and 6 of projects/status.py): change_status gives the status and
        # saves the task, with the reason it was just blocked as a comment by this admin, and this admin in the
        # history (what SimpleHistoryAdmin.save_model did). Given to someone outside the team, the task puts that
        # person in the team, remembered as added by this admin (see Task.save). No "unblocked" notification from
        # here (as before).
        change_status(obj, take_new_status(obj), by=request.user, reason=form.blocking_reason or "",
                      expected_end=form.blocking_expected_end)

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
