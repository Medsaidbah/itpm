"""Every change to the models comes with its migration.

The live app applies the migrations when it starts, on a database nobody here has a copy of. A model change
without its migration would leave that database out of step with the code: pages could fail on a missing
column. This test fails as soon as `makemigrations` would write something, so the migration is never forgotten."""

import io

from django.core.management import call_command
from django.test import TestCase


class MigrationTests(TestCase):
    # Our own apps only: a new version of a library (django-axes, simple_history) may bring its own migrations,
    # which are in the library itself, not in this project
    APPS = ["accounts", "projects"]

    def test_every_model_change_has_its_migration(self):
        output = io.StringIO()
        try:
            call_command("makemigrations", *self.APPS, check=True, dry_run=True, stdout=output, stderr=output)
        except SystemExit:
            self.fail(
                "A model changed without its migration. Run `python manage.py makemigrations` "
                "and check that the new migration keeps the existing data.\n" + output.getvalue()
            )
