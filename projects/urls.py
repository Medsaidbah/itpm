from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("projects/<int:pk>/edit/", views.project_edit, name="project_edit"),
    path("projects/<int:project_pk>/tasks/new/", views.task_create, name="task_create"),
    path("tasks/<int:pk>/", views.task_detail, name="task_detail"),
    path("tasks/<int:pk>/edit/", views.task_edit, name="task_edit"),
    path("tasks/<int:pk>/status/", views.task_status, name="task_status"),
    path("tasks/<int:pk>/delete/", views.task_delete, name="task_delete"),
    path("tasks/<int:task_pk>/comments/add/", views.comment_add, name="comment_add"),
    path("comments/<int:pk>/delete/", views.comment_delete, name="comment_delete"),
    path("tasks/<int:task_pk>/files/add/", views.attachment_add, name="attachment_add"),
    path("files/<int:pk>/delete/", views.attachment_delete, name="attachment_delete"),
]
