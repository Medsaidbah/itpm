from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.forms import LoginForm
from projects.views import attachment_download

from .health import healthz

urlpatterns = [
    path("healthz/", healthz, name="healthz"),  # for Docker / monitoring: "ok" when the database answers
    path("login/", auth_views.LoginView.as_view(authentication_form=LoginForm, redirect_authenticated_user=True),
         name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),  # Django's admin panel, a technical tool
    path("administration/", include("administration.urls")),  # the app's Administration section (admins only)
    # Uploaded files, only for people who are logged in (must match MEDIA_URL in settings.py)
    path("media/<path:path>", attachment_download, name="attachment_download"),
    path("", include("accounts.urls")),  # profiles and people's pages
    path("", include("projects.urls")),
]
