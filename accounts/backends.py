"""Logging in with the work email address (Stage 18a).

People type their email address (or still their username) and their password. Both are matched
without caring about capital letters, so "Sara.Ben@Company.mr" finds the account of sara.ben@company.mr.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


def find_account(identifier):
    """The account that this email address or username belongs to, or None.
    An exact username wins; then a username ignoring capitals; then the email address.
    If two accounts would match, nobody is returned: it is better to refuse than to guess."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    users = get_user_model().objects
    exact = users.filter(username=identifier).first()
    if exact:
        return exact
    for lookup in ("username__iexact", "email__iexact"):
        found = list(users.filter(**{lookup: identifier})[:2])
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
    return None


class EmailOrUsernameBackend(ModelBackend):
    """Django's usual login check, but the first box may hold an email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None
        account = find_account(username)
        if account is None:
            # Hash the password anyway, so a wrong name takes as long as a wrong password
            get_user_model()().set_password(password)
            return None
        if account.check_password(password) and self.user_can_authenticate(account):
            return account
        return None


def axes_username(request, credentials):
    """What django-axes counts wrong passwords under: the account's username, whether the person typed
    the username or the email address. Without this, 5 tries with each would be allowed.
    Something that is nobody's name is counted under what was typed, in small letters."""
    typed = (credentials or {}).get("username") or getattr(request, "POST", {}).get("username") or ""
    account = find_account(typed)
    return account.username if account else str(typed).strip().lower()
