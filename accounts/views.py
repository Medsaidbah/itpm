"""People: your own profile, the page of a person, profile pictures, and the language switch."""

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.messages.views import SuccessMessageMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from projects.models import Project, Task, people_visible_to

from .forms import ProfileForm, StyledPasswordChangeForm
from .middleware import LANGUAGE_CODES, remember_language
from .models import User


@login_required
def profile(request):
    """My profile: the picture and the phone number can be changed here.
    The names and the email are changed by an admin (emails are sent to that address)."""
    # A fresh copy of the person: if the form has a mistake, the rest of the page isn't affected
    me = User.objects.get(pk=request.user.pk)
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=me)
        if form.is_valid():
            form.save()
            # The message appears on the next page: in the language the person just chose (if they changed it)
            with translation.override(me.get_language()):
                messages.success(request, _("Your profile is saved."))
            return redirect("profile")
    else:
        form = ProfileForm(instance=me)
    return render(request, "accounts/profile.html", {"form": form})


class PasswordChangeView(SuccessMessageMixin, auth_views.PasswordChangeView):
    """Change my password: the old one first, then the new one twice."""

    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("profile")
    success_message = gettext_lazy("Your password is changed.")


@login_required
def person_detail(request, pk):
    """A person's page: their details and their projects. Only for the people who may see it
    (admins, the person, and the people they share a project with); for others it doesn't exist."""
    person = get_object_or_404(people_visible_to(request.user), pk=pk)
    # Their projects, among the ones the person who is logged in can see (admins: all of them)
    projects = Project.objects.visible_to(request.user).select_related("manager").order_by("name")
    context = {
        "person": person,
        "managed_projects": projects.filter(manager=person),
        "member_projects": projects.filter(memberships__user=person).exclude(manager=person),
        "open_tasks": Task.objects.visible_to(request.user)
        .filter(assignee=person)
        .exclude(status=Task.Status.DONE)
        .select_related("project")
        .order_by("due_date", "id"),
    }
    return render(request, "accounts/person_detail.html", context)


@login_required
def person_picture(request, pk):
    """A person's picture, for the people who may see their profile."""
    person = get_object_or_404(people_visible_to(request.user).exclude(picture=""), pk=pk)
    try:
        file = person.picture.open("rb")
    except OSError:
        raise Http404(_("This picture is missing from the server."))
    response = FileResponse(file, content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=86400"  # the browser keeps it for a day
    return response


@require_POST
def set_language(request):
    """The language switch, in the user menu and on the login page. The choice is saved in the
    person's profile (when they are logged in) and in the browser, then the same page opens again."""
    language = request.POST.get("language")
    # Back to the page the switch was on. Only addresses on this site are accepted.
    next_page = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_page, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        next_page = reverse_lazy("home")
    response = redirect(next_page)
    if language in LANGUAGE_CODES:
        if request.user.is_authenticated:
            User.objects.filter(pk=request.user.pk).update(language=language)
        remember_language(response, language)
    return response
