"""Profiles (Stage 17a): each person can have a phone number and a profile picture.
Two new columns in the users table, empty for everyone at first. Nothing else changes."""

from django.db import migrations, models

import accounts.models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_admin_and_user_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="phone",
            field=models.CharField(
                blank=True, max_length=30, validators=[accounts.models.validate_phone]
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="picture",
            field=models.ImageField(blank=True, upload_to="people/"),
        ),
    ]
