"""The email outbox (Step 26): every email is written in the table first (EmailOutbox), then sent.

- In the app, a background thread sends it right after the page is done, so nobody waits for the mail server.
- An email that fails is kept and tried again later (see retry_waiting), up to settings.EMAIL_MAX_ATTEMPTS times.
  Retries happen whenever another email is sent, and when `python manage.py send_outbox` runs (schedule it).
- Where an immediate answer is wanted (the automatic tests, the reminder job), the email is sent at once and an
  error is raised if it fails, as before.
"""

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import EmailOutbox

logger = logging.getLogger(__name__)

RETRY_MINUTES = 5  # after the 1st failure wait 5 minutes, after the 2nd 10 minutes, and so on
STUCK_MINUTES = 10  # an email "being sent" for longer than this is taken again (the sender died)
FORGOTTEN_MINUTES = 2  # an email nobody tried to send for this long (the server restarted) is sent
KEEP_SENT_DAYS = 30
KEEP_FAILED_DAYS = 7


def queue(to, subject, text, html):
    """Writes an email in the outbox."""
    return EmailOutbox.objects.create(to=to, subject=subject, text=text, html=html)


def deliver(pk, raise_errors=False):
    """Sends one email of the outbox. Returns True if it was sent.
    raise_errors=True: if the mail server says no, the error is raised, and the email is not tried again."""
    now = timezone.now()
    max_attempts = settings.EMAIL_MAX_ATTEMPTS
    # Only one sender takes an email (the database decides), so it is never sent twice
    claimed = EmailOutbox.objects.filter(
        Q(claimed_at__isnull=True) | Q(claimed_at__lt=now - timedelta(minutes=STUCK_MINUTES)),
        pk=pk, sent_at__isnull=True, attempts__lt=max_attempts,
    ).update(claimed_at=now)
    if not claimed:
        return False
    row = EmailOutbox.objects.get(pk=pk)
    try:
        message = EmailMultiAlternatives(row.subject, row.text, to=[row.to])
        if row.html:
            message.attach_alternative(row.html, "text/html")
        message.send()
    except Exception as error:
        row.attempts = max_attempts if raise_errors else row.attempts + 1
        row.last_attempt_at, row.last_error, row.claimed_at = timezone.now(), str(error)[:500], None
        row.save(update_fields=["attempts", "last_attempt_at", "last_error", "claimed_at"])
        if raise_errors:
            raise
        logger.warning("Email to %s failed (try %s of %s): %s", row.to, row.attempts, max_attempts, error)
        return False
    # Sent: the text is erased, who/what/when stay
    EmailOutbox.objects.filter(pk=pk).update(sent_at=timezone.now(), claimed_at=None, text="", html="", last_error="")
    return True


def waiting_to_retry():
    """The emails that should be tried again now."""
    now = timezone.now()
    rows = EmailOutbox.objects.filter(sent_at__isnull=True, attempts__lt=settings.EMAIL_MAX_ATTEMPTS)
    ready = []
    for row in rows.order_by("created_at")[:200]:
        if row.attempts == 0:
            due = row.created_at + timedelta(minutes=FORGOTTEN_MINUTES)
        else:
            due = (row.last_attempt_at or row.created_at) + timedelta(minutes=RETRY_MINUTES * row.attempts)
        if due <= now:
            ready.append(row.pk)
    return ready


def retry_waiting(limit=20):
    """Tries again the emails that failed (or were forgotten). Returns how many were sent."""
    return sum(deliver(pk) for pk in waiting_to_retry()[:limit])


def purge():
    """Deletes old rows: sent ones after 30 days, given-up ones after 7 days."""
    now = timezone.now()
    old_sent = EmailOutbox.objects.filter(sent_at__lt=now - timedelta(days=KEEP_SENT_DAYS))
    old_failed = EmailOutbox.objects.filter(
        sent_at__isnull=True, attempts__gte=settings.EMAIL_MAX_ATTEMPTS,
        created_at__lt=now - timedelta(days=KEEP_FAILED_DAYS),
    )
    return old_sent.delete()[0] + old_failed.delete()[0]


def unsent_now():
    """Every email still to send, whatever the waiting time (for the "Send again" button)."""
    return list(EmailOutbox.objects.filter(
        sent_at__isnull=True, attempts__lt=settings.EMAIL_MAX_ATTEMPTS).values_list("pk", flat=True))


def _work(pk, everything=False):
    try:
        if pk is not None:
            deliver(pk)
        if everything:
            sum(deliver(other) for other in unsent_now())
        else:
            retry_waiting()
    except Exception:  # a thread must never die silently
        logger.exception("The email sender had a problem")


def _thread_main(pk, everything):
    """What the sender thread runs: the work, then it closes the database connection it opened."""
    try:
        _work(pk, everything)
    finally:
        close_old_connections()
        connection.close()


def start_background(pk=None, everything=False):
    """Sends this email (and tries again the ones that failed) in a thread, once the page's data is saved.
    everything=True: every email still to send, without waiting for its turn."""
    transaction.on_commit(lambda: threading.Thread(target=_thread_main, args=(pk, everything), daemon=True).start())


def send_now_or_later(pk):
    """In the app: later, in a thread. In tests (EMAIL_IN_BACKGROUND false): now, and an error is raised."""
    if settings.EMAIL_IN_BACKGROUND:
        start_background(pk)
    else:
        deliver(pk, raise_errors=True)
