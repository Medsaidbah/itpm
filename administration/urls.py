"""The addresses of the Administration section. They all start with /administration/ (see config/urls.py)."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="admin_home"),
    path("users/", views.user_list, name="admin_users"),
    path("users/new/", views.user_create, name="admin_user_create"),
    path("users/import/", views.user_import, name="admin_user_import"),
    path("users/<int:pk>/", views.user_detail, name="admin_user"),
    path("users/<int:pk>/password/", views.user_password, name="admin_user_password"),
    path("users/<int:pk>/deactivate/", views.user_deactivate, name="admin_user_deactivate"),
    path("users/<int:pk>/reactivate/", views.user_reactivate, name="admin_user_reactivate"),
    path("users/<int:pk>/projects/add/", views.user_add_project, name="admin_user_add_project"),
    path("users/<int:pk>/projects/<int:project_pk>/remove/", views.user_remove_project,
         name="admin_user_remove_project"),
    path("projects/", views.project_list, name="admin_projects"),
    path("tasks/", views.task_list, name="admin_tasks"),
    path("feedback/", views.feedback_list, name="admin_feedback"),
    path("feedback/<int:pk>/review/", views.feedback_review, name="admin_feedback_review"),
    path("emails/", views.email_list, name="admin_emails"),
    path("emails/retry/", views.email_retry, name="admin_emails_retry"),
    path("logins/", views.login_list, name="admin_logins"),
    path("logins/unlock/", views.login_unlock, name="admin_unlock"),
]
