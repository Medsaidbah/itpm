"""The forms of the Administration section."""

from django import forms
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm, UsernameField
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from accounts.forms import PictureFormMixin
from accounts.models import NO_ADMIN_LEFT, User, leaves_no_active_admin
from accounts.pictures import MAX_PICTURE_MB
from projects.forms import BootstrapFormMixin
from projects.models import Project

ACCOUNT_FIELDS = ["username", "first_name", "last_name", "email", "phone", "picture", "role"]
ACCOUNT_LABELS = {"username": _("Username"), "phone": _("Phone number"), "role": _("Role")}
ACCOUNT_HELP = {
    "username": _("What they type to log in. Letters, digits and @ . + - _ only."),
    "role": _("Admins see and change everything, including this Administration section. "
              "Users see the projects they manage or whose team they are in."),
}


def picture_field():
    """The picture box of the account forms: a new picture is made small and square (see pictures.py)."""
    return forms.ImageField(
        label=_("Picture"),
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp,image/gif"}),
    )


class AccountFormMixin(PictureFormMixin):
    """What the two account forms (a new account, an account's details) have in common."""

    def set_up_fields(self):
        # Decided in Stage 17a: everyone has a first name, a last name and an email
        for name in ("first_name", "last_name", "email"):
            self.fields[name].required = True
        self.fields["picture"].help_text = gettext(
            "A JPG or PNG picture, up to %(size)s MB. It is cut to a square."
        ) % {"size": MAX_PICTURE_MB}
        # dir="ltr": a phone number, an email or a username reads from left to right, even in Arabic
        self.fields["phone"].widget.attrs.update(placeholder="+222 22 12 34 56", autocomplete="off", dir="ltr")
        self.fields["email"].widget.attrs.update(autocomplete="off", dir="ltr")
        self.fields["username"].widget.attrs["dir"] = "ltr"

    def clean_username(self):
        """Usernames must be different, even in capitals: "Sara" and "sara" can't both exist."""
        username = self.cleaned_data.get("username")
        if username and User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(self.instance.unique_error_message(User, ["username"]))
        return username

    def clean_email(self):
        """Two accounts can't share an email address (checked when the address is typed or changed)."""
        email = self.cleaned_data.get("email")
        if (
            email
            and "email" in self.changed_data
            and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists()
        ):
            raise forms.ValidationError(_("Another account already uses this email address."))
        return email


class NewAccountForm(AccountFormMixin, BootstrapFormMixin, UserCreationForm):
    """A new account: the person's details, their role, and a first password (typed twice)."""

    picture = picture_field()

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ACCOUNT_FIELDS
        labels = ACCOUNT_LABELS
        help_texts = ACCOUNT_HELP

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_up_fields()
        self.fields["password1"].label = _("First password")


class AccountForm(AccountFormMixin, BootstrapFormMixin, forms.ModelForm):
    """The details of an account, changed by an admin. The password, and whether the account
    is active, have their own buttons on the page."""

    picture = picture_field()
    remove_picture = forms.BooleanField(required=False, label=_("Remove the picture"))

    class Meta:
        model = User
        fields = ACCOUNT_FIELDS
        labels = ACCOUNT_LABELS
        help_texts = ACCOUNT_HELP
        field_classes = {"username": UsernameField}

    def __init__(self, *args, acting_user, **kwargs):
        """acting_user: the admin filling in the form."""
        super().__init__(*args, **kwargs)
        self.set_up_fields()
        role = self.fields["role"]
        # A field that is "disabled" is shown but can't change: Django ignores any value sent for it
        if self.instance.pk == acting_user.pk:
            role.disabled = True
            role.help_text = _(
                "You can't change your own role. Another admin can: this way the app always keeps an admin."
            )
        elif self.instance.is_superuser:
            role.disabled = True
            role.help_text = _("This account was made with the createsuperuser command, so it is always an admin.")

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role", self.instance.role)
        if leaves_no_active_admin(self.instance, role, self.instance.is_active):
            raise forms.ValidationError(NO_ADMIN_LEFT)
        return cleaned_data


class NewPasswordForm(BootstrapFormMixin, SetPasswordForm):
    """An admin gives someone a new password, for example when they forgot theirs."""


class AddToProjectForm(BootstrapFormMixin, forms.Form):
    """The "Add to a project" box of a person's page: the projects whose team they aren't in."""

    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),  # set below, for each person
        label=_("Project"),
        error_messages={
            "required": _("Choose a project."),
            "invalid_choice": _("This person can't be added to this project: they are in its team already."),
        },
    )

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person
        field = self.fields["project"]
        field.queryset = (
            Project.objects.exclude(manager=person).exclude(memberships__user=person).order_by("name")
        )
        # Two groups in the list: the projects that aren't finished, then the finished ones
        active = [(p.pk, p.name) for p in field.queryset if p.status in Project.ACTIVE_STATUSES]
        finished = [(p.pk, f"{p.name} ({p.get_status_display()})") for p in field.queryset
                    if p.status not in Project.ACTIVE_STATUSES]
        groups = [(_("Active projects"), active), (_("Finished projects"), finished)]
        field.choices = [("", _("Choose a project…"))] + [(label, options) for label, options in groups if options]

    def clean(self):
        cleaned_data = super().clean()
        if not self.person.is_active:
            raise forms.ValidationError(
                _("This account is deactivated. Reactivate it before adding the person to a project.")
            )
        return cleaned_data


class UnlockForm(forms.Form):
    """Sent by the Unlock buttons: the username and the internet address of the locked login."""

    username = forms.CharField(required=False, max_length=255)
    ip_address = forms.GenericIPAddressField(required=False)
