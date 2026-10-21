"""Stage 18a: temporary passwords, and the reading of a list of people to create in one go."""

import csv
import secrets
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext as _

from accounts.models import User

# No 0/O, 1/l/I: a password read aloud or from paper is not mistaken
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MAX_IMPORT_LINES = 200


def temporary_password():
    """A new random password like "k7mp-x3qa-h9ne" (about 59 bits: enough, it must be changed at first login)."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))


@dataclass
class ImportResult:
    rows: list = field(default_factory=list)    # (line number, first name, last name, email)
    errors: list = field(default_factory=list)  # (line number, message)


def _split(line):
    for delimiter in ("\t", ";", ","):
        if delimiter in line:
            return [part.strip() for part in next(csv.reader([line], delimiter=delimiter))]
    return [line.strip()]


def read_people(text):
    """Reads "first name, last name, email" lines (separated by a tab, ; or ,). A first line that has
    no @ is a header row and is skipped. Nothing is created here: the mistakes of ALL the lines are
    collected, so the admin fixes the list once."""
    result = ImportResult()
    lines = [(n, line) for n, line in enumerate(text.splitlines(), start=1) if line.strip()]
    if lines and "@" not in lines[0][1]:
        lines = lines[1:]
    if not lines:
        result.errors.append((0, _("The list is empty.")))
        return result
    if len(lines) > MAX_IMPORT_LINES:
        result.errors.append((0, _("The list has more than %(max)s people. Split it in several lists.")
                              % {"max": MAX_IMPORT_LINES}))
        return result
    seen = {}
    for number, line in lines:
        parts = _split(line)
        if len(parts) != 3 or not all(parts):
            result.errors.append((number, _("Expected: first name, last name, email address.")))
            continue
        first, last, email = parts
        try:
            validate_email(email)
        except ValidationError:
            result.errors.append((number, _("%(email)s is not a valid email address.") % {"email": email}))
            continue
        key = email.lower()
        if key in seen:
            result.errors.append((number, _("%(email)s is already on line %(line)s.")
                                  % {"email": email, "line": seen[key]}))
            continue
        seen[key] = number
        username_taken = User.objects.filter(username__iexact=key).exists()
        if username_taken or User.objects.filter(email__iexact=email).exists():
            result.errors.append((number, _("An account already uses %(email)s.") % {"email": email}))
            continue
        if len(first) > 150 or len(last) > 150 or len(email) > 150:
            result.errors.append((number, _("A value is too long.")))
            continue
        result.rows.append((number, first, last, email))
    return result
