from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Attachment, Comment, Project, ProjectMember, Task


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ["title", "status", "assignee", "start_date", "due_date"]
    show_change_link = True


class ProjectMemberInline(admin.TabularInline):
    """The project's team, below the project in the admin panel. The manager is chosen above
    (Manager field) and is always part of the team, so there's no need to add them here."""

    model = ProjectMember
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
        # Remember who added each new team member
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, ProjectMember) and obj.pk is None:
                obj.added_by = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


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
