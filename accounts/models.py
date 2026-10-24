from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy

from .middleware import chosen_language


def validate_phone(value):
    """A phone number: digits, spaces and + - . ( ) only, with at least 6 digits."""
    allowed = set("0123456789 +-.()")
    if not set(value) <= allowed or sum(character.isdigit() for character in value) < 6:
        raise ValidationError(
            gettext("Enter a phone number with digits, spaces and + - ( ) only, like +222 22 12 34 56.")
        )


class User(AbstractUser):
    # The role in the whole app. Being a project's manager or a member of its team
    # is decided project by project (Project.manager and the project's team).
    class Role(models.TextChoices):
        ADMIN = "admin", gettext_lazy("Admin")
        USER = "user", gettext_lazy("User")

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)
    phone = models.CharField(max_length=30, blank=True, validators=[validate_phone])
    # A small square JPEG made by the app (see pictures.py), never the uploaded file itself
    picture = models.ImageField(upload_to="people/", blank=True)
    # The language of the person's pages and emails (Stage 17c): "en", "fr" or "ar".
    # Empty until they choose one: then the first login saves the language of the login page.
    language = models.CharField(max_length=10, choices=settings.LANGUAGES, blank=True)
    # Stage 18a: a password an administrator chose or generated is only a temporary one. Until the person
    # replaces it with their own, they can't use the app (see PasswordChangeRequiredMiddleware), and it
    # stops working after TEMPORARY_PASSWORD_DAYS days if they never log in.
    must_change_password = models.BooleanField(default=False)
    temporary_password_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def is_admin(self):
        # Admins can do everything, in every project
        return self.role == self.Role.ADMIN

    def mark_password_as_temporary(self):
        """Call after set_password() when somebody else chose the password: the person must replace it.
        Nothing is saved here."""
        self.must_change_password = True
        self.temporary_password_until = timezone.now() + timedelta(days=settings.TEMPORARY_PASSWORD_DAYS)

    def mark_password_as_own(self):
        """Call after the person chose their own password. Nothing is saved here."""
        self.must_change_password = False
        self.temporary_password_until = None

    @property
    def temporary_password_expired(self):
        return bool(
            self.must_change_password
            and self.temporary_password_until
            and timezone.now() > self.temporary_password_until
        )

    def get_language(self):
        """The language of this person's pages and emails: their choice, or English if they have none yet."""
        return self.language or settings.LANGUAGE_CODE

    @property
    def initials(self):
        """Shown instead of a picture: "SB" for Sara Ben, or the first letter of the username."""
        letters = self.first_name[:1] + self.last_name[:1]
        return (letters or self.username[:1]).upper()

    def save(self, *args, **kwargs):
        # A superuser is always an admin in the app
        if self.is_superuser:
            self.role = self.Role.ADMIN

        # The picture this person had before this save (to delete its file if it changes)
        update_fields = kwargs.get("update_fields")
        old_picture = ""
        if self.pk and (update_fields is None or "picture" in update_fields):
            old_picture = User.objects.filter(pk=self.pk).values_list("picture", flat=True).first() or ""
        super().save(*args, **kwargs)

        if old_picture and old_picture != self.picture.name:
            # Delete the old file once the database change is final ("on commit")
            storage = self.picture.storage
            transaction.on_commit(lambda: storage.delete(old_picture))


# Shown when a change would leave the app without any active admin
class Feedback(models.Model):
    """What someone thinks of the app (Step 25): a rating from 1 to 5, a kind of remark, and a message.
    Only admins read it. With "send without my name", nobody is saved as the author."""

    class Kind(models.TextChoices):
        PRAISE = "praise", gettext_lazy("Something I like")
        IDEA = "idea", gettext_lazy("An idea or a suggestion")
        PROBLEM = "problem", gettext_lazy("Something that doesn't work")
        OTHER = "other", gettext_lazy("Something else")

    RATINGS = (
        (1, gettext_lazy("Very bad")),
        (2, gettext_lazy("Bad")),
        (3, gettext_lazy("Fine")),
        (4, gettext_lazy("Good")),
        (5, gettext_lazy("Excellent")),
    )
    MAX_MESSAGE = 2000

    author = models.ForeignKey(  # empty when sent without a name, or when that account was deleted
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="feedback"
    )
    rating = models.PositiveSmallIntegerField(choices=RATINGS)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    message = models.TextField(blank=True, max_length=MAX_MESSAGE)
    page = models.CharField(max_length=200, blank=True)  # the address of the page the person was on
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "feedback"
        constraints = [
            models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name="feedback_rating_1_to_5")
        ]

    def __str__(self):
        return f"{self.rating}/5 · {self.get_kind_display()}"


NO_ADMIN_LEFT = gettext_lazy("The app needs at least one active admin. Give the Admin role to someone else first.")


def leaves_no_active_admin(person, role, is_active):
    """True if giving this person this role and this active status would leave the app without
    any active admin. Then nobody could manage the people and the projects anymore, so it's refused."""
    if role == User.Role.ADMIN and is_active:
        return False
    return not User.objects.filter(role=User.Role.ADMIN, is_active=True).exclude(pk=person.pk).exists()


@receiver(user_logged_in)
def keep_the_login_page_language(sender, request, user, **kwargs):
    """Someone who logs in without having chosen a language yet keeps the language of the login page
    (the one chosen on this computer, or English): it is saved in their profile, for their emails too."""
    if request is None:
        return
    language = chosen_language(request)
    # "language=''" in the filter: the database itself says nothing is saved yet. A language that is already in
    # the profile is never replaced (even if the copy of the person we were given is out of date).
    if User.objects.filter(pk=user.pk, language="").update(language=language):
        user.language = language


@receiver(post_delete, sender=User)
def delete_picture_file(sender, instance, **kwargs):
    """When someone is deleted, their picture's file is deleted from the disk too."""
    name, storage = instance.picture.name, instance.picture.storage
    if name:
        transaction.on_commit(lambda: storage.delete(name))
