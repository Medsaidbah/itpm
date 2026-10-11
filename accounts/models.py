from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy


def validate_phone(value):
    """A phone number: digits, spaces and + - . ( ) only, with at least 6 digits."""
    allowed = set("0123456789 +-.()")
    if not set(value) <= allowed or sum(character.isdigit() for character in value) < 6:
        raise ValidationError("Enter a phone number with digits, spaces and + - ( ) only, like +222 22 12 34 56.")


class User(AbstractUser):
    # The role in the whole app. Being a project's manager or a member of its team
    # is decided project by project (Project.manager and the project's team).
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        USER = "user", "User"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)
    phone = models.CharField(max_length=30, blank=True, validators=[validate_phone])
    # A small square JPEG made by the app (see pictures.py), never the uploaded file itself
    picture = models.ImageField(upload_to="people/", blank=True)

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def is_admin(self):
        # Admins can do everything, in every project
        return self.role == self.Role.ADMIN

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
NO_ADMIN_LEFT = gettext_lazy("The app needs at least one active admin. Give the Admin role to someone else first.")


def leaves_no_active_admin(person, role, is_active):
    """True if giving this person this role and this active status would leave the app without
    any active admin. Then nobody could manage the people and the projects anymore, so it's refused."""
    if role == User.Role.ADMIN and is_active:
        return False
    return not User.objects.filter(role=User.Role.ADMIN, is_active=True).exclude(pk=person.pk).exists()


@receiver(post_delete, sender=User)
def delete_picture_file(sender, instance, **kwargs):
    """When someone is deleted, their picture's file is deleted from the disk too."""
    name, storage = instance.picture.name, instance.picture.storage
    if name:
        transaction.on_commit(lambda: storage.delete(name))
