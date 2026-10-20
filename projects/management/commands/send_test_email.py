"""python manage.py send_test_email someone@company.mr

Sends one short test email with the email settings of this installation, and says what happened.
Use it after setting up the email (Microsoft 365: see EMAIL-M365-SETUP.md).
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Sends one test email to the given address, to check the email settings."

    def add_arguments(self, parser):
        parser.add_argument("address", help="Who receives the test email.")

    def handle(self, *args, **options):
        self.stdout.write(f"Email backend: {settings.EMAIL_BACKEND}")
        if settings.EMAIL_BACKEND.endswith("console.EmailBackend"):
            warning = "This is the console backend: the email is only printed below, not sent."
            self.stdout.write(self.style.WARNING(warning))
        try:
            send_mail(
                "IT Projects: test email",
                "If you read this, the email settings of IT Projects work.",
                None,
                [options["address"]],
            )
        except Exception as error:
            raise CommandError(f"The test email could not be sent: {error}") from None
        self.stdout.write(self.style.SUCCESS(f"Test email accepted for {options['address']}."))
