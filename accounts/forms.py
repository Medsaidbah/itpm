from django import forms
from django.contrib.auth.forms import PasswordChangeForm, UserChangeForm

from projects.forms import BootstrapFormMixin

from .models import User
from .pictures import MAX_PICTURE_MB, make_profile_picture


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
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp,image/gif"}),
        help_text=f"A JPG or PNG picture, up to {MAX_PICTURE_MB} MB. It is cut to a square.",
    )
    remove_picture = forms.BooleanField(required=False, label="Remove my picture")

    class Meta:
        model = User
        fields = ["phone", "picture"]
        labels = {"phone": "Phone number"}
        widgets = {"phone": forms.TextInput(attrs={"placeholder": "+222 22 12 34 56", "autocomplete": "tel"})}


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    """Django's "change your password" form, with the app's look."""


class UserAdminChangeForm(PictureFormMixin, UserChangeForm):
    """The person's page in Django's admin panel: a new picture is made small and square too."""
