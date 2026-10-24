"""python manage.py send_outbox

Tries again the emails that could not be sent (the mail server was down, for example) and deletes old rows of the
outbox. The app already does it whenever another email is sent: schedule this command every 10 minutes or so
(like send_reminders) so that nothing waits for the next email.
    python manage.py send_outbox --status    only shows how many emails wait, failed, were sent
"""

from django.core.management.base import BaseCommand

from projects import outbox
from projects.models import EmailOutbox


class Command(BaseCommand):
    help = "Tries again the emails that failed and cleans the outbox."

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true", help="Only show the numbers.")

    def handle(self, *args, **options):
        if not options["status"]:
            sent = outbox.retry_waiting(limit=200)
            deleted = outbox.purge()
            self.stdout.write(f"{sent} email(s) sent again, {deleted} old row(s) deleted.")
        counts = {"waiting": 0, "failed": 0, "sent": 0}
        for row in EmailOutbox.objects.all():
            counts[row.state] += 1
        self.stdout.write(f"Outbox: {counts['waiting']} waiting, {counts['failed']} failed, {counts['sent']} sent.")
