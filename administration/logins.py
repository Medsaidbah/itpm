"""Wrong passwords and locked logins, read from django-axes (see settings.py, "People and logging in").

Axes writes a line for each username and internet address (IP) that typed a wrong password.
After AXES_FAILURE_LIMIT wrong passwords within AXES_COOLOFF_TIME, logging in with that username
from that address is locked, until the oldest of those wrong passwords is old enough.
Deleting the lines unlocks it at once.
"""

from dataclasses import dataclass
from datetime import datetime

from axes.helpers import get_cool_off, get_failure_limit
from axes.models import AccessAttempt
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone


@dataclass
class FailedLogins:
    """The recent wrong passwords of one username, from one internet address."""

    username: str  # what was typed: it may not be anyone's username
    ip_address: str
    failures: int
    last_try: datetime
    locked_until: datetime | None  # None when it isn't locked
    account: object = None  # the person with this username, if there is one

    @property
    def locked(self):
        return self.locked_until is not None


def failed_logins(request, username=None):
    """The wrong passwords that still count (the recent ones), newest first.
    username: only the ones typed with this username."""
    limit = get_failure_limit(request, None)
    window = get_cool_off(request)  # how long a wrong password counts (1 hour)
    attempts = AccessAttempt.objects.filter(attempt_time__gte=timezone.now() - window).order_by("attempt_time")
    if username is not None:
        attempts = attempts.filter(username=username)

    # One line per username and address (axes may have several: one per web browser)
    groups = {}
    for attempt in attempts:
        groups.setdefault((attempt.username or "", attempt.ip_address or ""), []).append(attempt)

    result = []
    for (typed_username, ip_address), lines in groups.items():
        failures = sum(line.failures_since_start for line in lines)
        locked_until = None
        if failures >= limit:
            # The lock ends when the oldest lines stop counting and fewer than `limit` are left
            left = failures
            for line in lines:  # oldest first
                left -= line.failures_since_start
                if left < limit:
                    locked_until = line.attempt_time + window
                    break
        result.append(FailedLogins(typed_username, ip_address, failures, lines[-1].attempt_time, locked_until))

    accounts = get_user_model().objects.in_bulk([group.username for group in result], field_name="username")
    for group in result:
        group.account = accounts.get(group.username)
    return sorted(result, key=lambda group: group.last_try, reverse=True)


def unlock(username, ip_address=""):
    """Forgets the wrong passwords of this username (only from this address, if one is given),
    which unlocks the login at once. Returns how many lines of axes were deleted."""
    same_username = Q(username=username) if username else Q(username="") | Q(username__isnull=True)
    attempts = AccessAttempt.objects.filter(same_username)
    if ip_address:
        attempts = attempts.filter(ip_address=ip_address)
    deleted, _per_table = attempts.delete()
    return deleted
