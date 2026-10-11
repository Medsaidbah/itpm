from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    # The role in the whole app. Being a project's manager or a member of its team
    # is decided project by project (Project.manager and the project's team).
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        USER = "user", "User"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def is_admin(self):
        # Admins can do everything, in every project
        return self.role == self.Role.ADMIN

    def save(self, *args, **kwargs):
        # A superuser is always an admin in the app
        if self.is_superuser:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)
