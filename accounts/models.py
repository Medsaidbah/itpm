from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        MEMBER = "member", "Member"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def can_manage(self):
        # Admins and managers can create projects and assign work
        return self.role in (self.Role.ADMIN, self.Role.MANAGER)

    @property
    def is_admin(self):
        # Admins can do everything, in every project
        return self.role == self.Role.ADMIN

    def save(self, *args, **kwargs):
        # A superuser is always an admin in the app
        if self.is_superuser:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)
