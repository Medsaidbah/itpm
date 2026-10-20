"""Sends Django emails through Microsoft 365 (Microsoft Graph "sendMail").

How it works (Step 15d):
  1. The app proves who it is to Microsoft with its tenant id, client id and client secret
     ("client credentials": no person logs in) and receives an access token (valid about an hour,
     kept in memory and renewed by itself).
  2. For each email it calls   POST {GRAPH_API_URL}/users/{GRAPH_SENDER}/sendMail   with that token.
     The email leaves from the mailbox GRAPH_SENDER (for example it-projects@company.mr).
  3. Microsoft answers "202 Accepted" when it takes the email. Anything else is an error.

Turn it on in .env / the container settings:
    EMAIL_BACKEND=config.graph_email.GraphEmailBackend
    GRAPH_TENANT_ID=...  GRAPH_CLIENT_ID=...  GRAPH_CLIENT_SECRET=...  GRAPH_SENDER=...
The Microsoft 365 administrator's steps are in EMAIL-M365-SETUP.md. Check with:
    python manage.py send_test_email someone@company.mr

Only the Python standard library is used. The secret is never written in a log or an error message.
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

REQUIRED = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SENDER")
MAX_WAIT_SECONDS = 10  # the longest we wait when Microsoft says "too many requests, try later"

_token_lock = threading.Lock()
_token = {"value": None, "expires_at": 0.0}  # one token for the whole process


class GraphEmailError(Exception):
    """Microsoft refused or could not be reached. The message says why (never the secret)."""


def forget_token():
    """Throws the saved access token away (a new one is asked for at the next email)."""
    with _token_lock:
        _token.update(value=None, expires_at=0.0)


def _setting(name):
    return (getattr(settings, name, "") or "").strip()


def _check_configuration():
    missing = [name for name in REQUIRED if not _setting(name)]
    if missing:
        raise ImproperlyConfigured(
            "Microsoft 365 email is not configured: these settings are empty: " + ", ".join(missing)
        )
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", _setting("GRAPH_TENANT_ID")):
        raise ImproperlyConfigured("GRAPH_TENANT_ID must be the tenant's id (letters, digits, dots, dashes).")
    if "@" not in _setting("GRAPH_SENDER"):
        raise ImproperlyConfigured("GRAPH_SENDER must be the sending mailbox's email address.")


def _explain(error):
    """A short, safe description of what Microsoft answered (Graph and the sign-in service use different shapes)."""
    try:
        body = json.loads(error.read().decode("utf-8", "replace"))
    except Exception:
        return f"HTTP {error.code}"
    if isinstance(body.get("error"), dict):  # Graph: {"error": {"code": ..., "message": ...}}
        code, message = body["error"].get("code", ""), body["error"].get("message", "")
    else:  # sign-in service: {"error": ..., "error_description": ...}
        code, message = body.get("error", ""), body.get("error_description", "")
    message = " ".join(str(message).split())[:300]
    return f"HTTP {error.code} {code}: {message}".strip()


def _call(request, timeout):
    """Sends a request. On "too many requests" or a short Microsoft outage, waits a little and tries once more."""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            if attempt == 1 and error.code in (429, 503, 504):
                try:
                    wait = min(float(error.headers.get("Retry-After", 2)), MAX_WAIT_SECONDS)
                except ValueError:
                    wait = 2
                time.sleep(max(wait, 0))
                continue
            raise GraphEmailError(_explain(error)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GraphEmailError(f"Could not reach Microsoft ({error.__class__.__name__})") from None


def _access_token(timeout):
    with _token_lock:
        if _token["value"] and time.monotonic() < _token["expires_at"]:
            return _token["value"]
        authority = _setting("GRAPH_AUTHORITY") or "https://login.microsoftonline.com"
        data = urllib.parse.urlencode(
            {
                "client_id": _setting("GRAPH_CLIENT_ID"),
                "client_secret": _setting("GRAPH_CLIENT_SECRET"),
                "scope": _setting("GRAPH_SCOPE") or "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }
        ).encode()
        request = urllib.request.Request(
            f"{authority.rstrip('/')}/{_setting('GRAPH_TENANT_ID')}/oauth2/v2.0/token", data=data, method="POST"
        )
        try:
            _, answer = _call(request, timeout)
        except GraphEmailError as error:
            raise GraphEmailError(f"Could not get an access token: {error}") from None
        if not answer.get("access_token"):
            raise GraphEmailError("Microsoft answered without an access token.")
        _token["value"] = answer["access_token"]
        # renew one minute early
        _token["expires_at"] = time.monotonic() + max(int(answer.get("expires_in", 3600)) - 60, 0)
        return _token["value"]


def _recipients(addresses):
    people = []
    for item in addresses:
        name, address = parseaddr(item)
        entry = {"address": address or item}
        if name:
            entry["name"] = name
        people.append({"emailAddress": entry})
    return people


def build_message(message):
    """Django's email -> the JSON that Graph wants. HTML is used when the email has an HTML version."""
    if message.attachments:
        raise GraphEmailError("Attachments are not supported by this email backend.")
    html = next((content for content, kind in getattr(message, "alternatives", []) if kind == "text/html"), None)
    if html is None and message.content_subtype == "html":
        html = message.body
    if html is not None:
        body = {"contentType": "HTML", "content": html}
    else:
        body = {"contentType": "Text", "content": message.body}
    payload = {"subject": message.subject, "body": body, "toRecipients": _recipients(message.to)}
    if message.cc:
        payload["ccRecipients"] = _recipients(message.cc)
    if message.bcc:
        payload["bccRecipients"] = _recipients(message.bcc)
    if message.reply_to:
        payload["replyTo"] = _recipients(message.reply_to)
    return payload


class GraphEmailBackend(BaseEmailBackend):
    """Django email backend for Microsoft 365. Used through EMAIL_BACKEND in the settings."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        try:
            _check_configuration()
        except ImproperlyConfigured:
            if self.fail_silently:
                return 0
            raise
        sent = 0
        for message in email_messages:
            if not (message.to or message.cc or message.bcc):
                continue
            try:
                self._send(message)
            except Exception as error:
                # A refusal from Microsoft is described by its message; anything unexpected gets its full trace
                logger.error("Microsoft 365 email failed: %s", error, exc_info=not isinstance(error, GraphEmailError))
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent

    def _send(self, message):
        timeout = getattr(settings, "EMAIL_TIMEOUT", 10)
        api = (_setting("GRAPH_API_URL") or "https://graph.microsoft.com/v1.0").rstrip("/")
        url = f"{api}/users/{urllib.parse.quote(_setting('GRAPH_SENDER'), safe='@')}/sendMail"
        save_to_sent_items = bool(getattr(settings, "GRAPH_SAVE_TO_SENT_ITEMS", False))
        data = json.dumps({"message": build_message(message), "saveToSentItems": save_to_sent_items}).encode()
        for attempt in (1, 2):
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Authorization": f"Bearer {_access_token(timeout)}",
                    "Content-Type": "application/json",
                },
            )
            try:
                status, _ = _call(request, timeout)
            except GraphEmailError as error:
                # An old token (for example revoked): ask for a new one and try once more
                if attempt == 1 and str(error).startswith("HTTP 401"):
                    forget_token()
                    continue
                raise
            if status != 202:
                raise GraphEmailError(f"Microsoft answered HTTP {status} instead of 202.")
            return
