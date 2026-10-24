from django.urls import path

from . import views

urlpatterns = [
    path("profile/", views.profile, name="profile"),
    path("profile/notifications/", views.notification_settings, name="notification_settings"),
    path("profile/password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("people/<int:pk>/", views.person_detail, name="person_detail"),
    path("people/<int:pk>/picture/", views.person_picture, name="person_picture"),
    path("feedback/", views.feedback, name="feedback"),
    path("language/", views.set_language, name="set_language"),
]
