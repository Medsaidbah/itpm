import logging

from django import forms
from django.conf import settings
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserChangeForm,
)
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import get_language, gettext
from django.utils.translation import gettext_lazy as _

from projects.forms import BootstrapFormMixin

from .models import NO_ADMIN_LEFT, User, leaves_no_active_admin
from .pictures import MAX_PICTURE_MB, make_profile_picture

logger = logging.getLogger(__name__)


def picture_help_text():
    """The hint under the picture boxes, in the language of the page."""
    return gettext("A JPG or PNG picture, up to %(size)s MB. It is cut to a square.") % {"size": MAX_PICTURE_MB}


class PictureFormMixin:
    """For forms with a "picture" field: the uploaded picture is replaced by a small square JPEG
    (see pictures.py), and a "remove_picture" tick box (when the form has one) removes it."""

    def clean_picture(self):
        picture = self.cleaned_data.get("picture")
        if picture and picture != self.initial.get("picture"):  # a new file was uploaded
            return make_profile_picture(picture)
        return picture

    def save(self, commit=True):
        if self.cleaned_data.get("remove_picture") and not self.files.get("picture"):
            self.instance.picture = ""  # no picture (the old file is deleted, see User.save)
        return super().save(commit)


class ProfileForm(PictureFormMixin, BootstrapFormMixin, forms.ModelForm):
    """What people change in their own profile. Names and email are changed by an admin."""

    picture = forms.ImageField(
        label=_("Picture"),
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp,image/gif"}),
    )
    remove_picture = forms.BooleanField(required=False, label=_("Remove my picture"))
    language = forms.ChoiceField(
        label=_("Language"),
        choices=settings.LANGUAGES,
        required=False,  # a form sent without it (an old page, a script) keeps the language as it is
        help_text=_("The language of your pages and of the emails you get."),
    )

    class Meta:
        model = User
        fields = ["phone", "picture", "language"]
        labels = {"phone": _("Phone number")}
        # dir="ltr": a phone number reads from left to right, even in Arabic
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "+222 22 12 34 56", "autocomplete": "tel", "dir": "ltr"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["picture"].help_text = picture_help_text()
        if not self.instance.language:
            # Not chosen yet: the language of this page
            self.initial["language"] = get_language()

    def clean_language(self):
        return self.cleaned_data["language"] or self.instance.language


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    """Django's "change your password" form, with the app's look. Whoever uses it chooses their own
    password, so a temporary password stops being temporary."""

    def clean(self):
        cleaned_data = super().clean()
        old_password = cleaned_data.get("old_password")
        new_password = cleaned_data.get("new_password1")
        if self.user.must_change_password and old_password and new_password == old_password:
            self.add_error("new_password1", ValidationError(
                _("Choose a password that is different from the temporary one."), code="same_password"))
        return cleaned_data

    def save(self, commit=True):
        self.user.mark_password_as_own()
        return super().save(commit)


# One reset email per account every two minutes: the form can't be used to flood someone's inbox
RESET_EMAIL_PAUSE_SECONDS = 120


class StyledPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    """"Forgot my password": the work email address. The email is sent in the language of the person's
    profile, with a link that starts with SITE_URL (not with the address the browser used)."""

    email = forms.EmailField(label=_("Work email address"), max_length=254, widget=forms.EmailInput(
        attrs={"autocomplete": "email", "dir": "ltr", "autofocus": True}))

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from projects.emails import email_password_reset  # here, because projects imports accounts

        person = context["user"]
        if not cache.add(f"password-reset-email-{person.pk}", 1, RESET_EMAIL_PAUSE_SECONDS):
            return  # one was just sent
        link = settings.SITE_URL.rstrip("/") + reverse(
            "password_reset_confirm", kwargs={"uidb64": context["uid"], "token": context["token"]})
        try:
            email_password_reset(person, link)
        except Exception:  # the page answers the same whatever happens, so nobody can probe for accounts
            logger.exception("Couldn't send the password reset email to %s", to_email)


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    """Choosing a new password from the email link. It is the person's own choice, so a temporary
    password stops being temporary."""

    def save(self, commit=True):
        self.user.mark_password_as_own()
        return super().save(commit)


class LoginForm(AuthenticationForm):
    """The login page: the work email address (or the username) and the password (Stage 18a)."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _(
            "Please enter a correct work email address (or username) and password. "
            "Note that the password is case-sensitive."
        ),
        "expired": _("Your temporary password has expired. Ask an admin for a new one."),
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        # The password is right, but a temporary one is only valid for a few days
        if user.temporary_password_expired:
            raise ValidationError(self.error_messages["expired"], code="expired")


class UserAdminChangeForm(PictureFormMixin, UserChangeForm):
    """The person's page in Django's admin panel: a new picture is made small and square too,
    and the last active admin can't lose the Admin role or be deactivated there."""

    def clean(self):
        cleaned_data = super().clean()
        # A superuser is always an admin (see User.save)
        superuser = cleaned_data.get("is_superuser", self.instance.is_superuser)
        role = User.Role.ADMIN if superuser else cleaned_data.get("role", self.instance.role)
        is_active = cleaned_data.get("is_active", self.instance.is_active)
        if leaves_no_active_admin(self.instance, role, is_active):
            raise forms.ValidationError(NO_ADMIN_LEFT)
        return cleaned_data
