"""python manage.py send_reminders

Emails each person their overdue tasks and the tasks due soon.
Managers also get the late tasks of the projects they manage.
People with nothing to be reminded of get no email.

Run it once a day (on the server, a scheduled job will do it: Step 15). To test:
    python manage.py send_reminders --dry-run      shows who would get an email, sends nothing
    python manage.py send_reminders --user sara    only for one person
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from projects.emails import reminder_for, send_email


class Command(BaseCommand):
    help = "Emails each person their overdue tasks and the tasks due soon. Run it once a day."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show who would get an email, send nothing.")
        parser.add_argument("--user", help="Only this username (to test).")

    def handle(self, *args, **options):
        today = timezone.localdate()
        people = get_user_model().objects.filter(is_active=True).order_by("username")
        if options["user"]:
            people = people.filter(username=options["user"])
            if not people:
                raise CommandError(f'There is no active user called "{options["user"]}".')

        sent = failed = 0
        for person in people:
            reminder = reminder_for(person, today)
            if not reminder["sections"]:
                continue  # nothing to remind this person of
            subject = reminder["subject"]
            if not person.email:
                self.stdout.write(self.style.WARNING(f"{person.username}: no email address, skipped ({subject})"))
                continue
            if options["dry_run"]:
                self.stdout.write(f'{person.username} <{person.email}>: would get "{subject}"')
                continue
            try:
                send_email(person.email, subject, "reminder", reminder)
            except Exception as error:  # like a mail server that is down: say so, and go on with the others
                failed += 1
                self.stderr.write(f"{person.username}: the email couldn't be sent ({error})")
            else:
                sent += 1
                self.stdout.write(self.style.SUCCESS(f'{person.username} <{person.email}>: sent "{subject}"'))

        if not options["dry_run"]:
            self.stdout.write(f"{sent} email{'' if sent == 1 else 's'} sent.")
        if failed:
            # An error at the end makes the scheduled job show that something went wrong
            raise CommandError(f"{failed} email{'' if failed == 1 else 's'} couldn't be sent.")
