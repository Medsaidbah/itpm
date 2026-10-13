from django import forms
from django.conf import settings
from django.contrib.auth.forms import PasswordChangeForm, UserChangeForm
from django.utils.translation import get_language, gettext
from django.utils.translation import gettext_lazy as _

from projects.forms import BootstrapFormMixin

from .models import NO_ADMIN_LEFT, User, leaves_no_active_admin
from .pictures import MAX_PICTURE_MB, make_profile_picture


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
    """Django's "change your password" form, with the app's look."""


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
