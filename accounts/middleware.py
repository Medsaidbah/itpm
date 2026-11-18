"""The language of each page (Stage 17c).

- Someone who is logged in sees the language saved in their profile.
- The login page shows the language last chosen on this computer (a cookie), or English.
The browser's own language isn't used: the app stays in English until people choose another one.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils import translation
from django.utils.cache import patch_vary_headers

LANGUAGE_CODES = {code for code, name in settings.LANGUAGES}


def chosen_language(request):
    """The language for this request, following the rules above."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.language in LANGUAGE_CODES:
        return user.language
    remembered = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    return remembered if remembered in LANGUAGE_CODES else settings.LANGUAGE_CODE


def remember_language(response, language):
    """Writes the language in the browser's cookie, so the login page uses it next time."""
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )


class LanguageMiddleware:
    """Puts the page in the chosen language. Placed after AuthenticationMiddleware in settings.py,
    because it needs to know who is logged in."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = chosen_language(request)
        request.LANGUAGE_CODE = language
        # Everything made for this page (texts, dates, error pages) uses this language.
        # "override" puts the previous language back afterwards.
        with translation.override(language):
            response = self.get_response(request)
        response.headers.setdefault("Content-Language", language)
        # The page depends on who is logged in and on the language cookie, both carried by cookies: this tells a
        # shared cache (a proxy) not to hand one person's page to someone else.
        patch_vary_headers(response, ("Cookie",))
        # Keep the cookie up to date (unless the page just set it, like the language switch does)
        if (
            request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME) != language
            and settings.LANGUAGE_COOKIE_NAME not in response.cookies
        ):
            remember_language(response, language)
        return response


class PasswordChangeRequiredMiddleware:
    """Stage 18a: someone whose password was chosen by an administrator (a temporary password) can't use
    the app until they have replaced it with their own. Every address except the ones below sends them to
    the "change my password" page: typing an address by hand doesn't get around it. Placed after the
    language middleware in settings.py, because it needs to know who is logged in."""

    # The password page itself, logging out, the language switch, and the health check
    ALLOWED = {"password_change", "logout", "set_language", "healthz"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_change_password and not self.allowed(request):
            return redirect("password_change")
        return self.get_response(request)

    def allowed(self, request):
        try:
            return resolve(request.path_info).view_name in self.ALLOWED
        except Resolver404:
            return False


class ReadOnlyRoleMiddleware:
    """Release 1.2: an executive reads everything and changes nothing. Every request that could change something
    (anything but GET, HEAD and OPTIONS) is refused with "No access", whatever the page, except the few below,
    which only concern their own account. The pages also hide the buttons, but this is the rule that counts:
    a forgotten button can't become a change. Placed after the password middleware in settings.py."""

    # Logging out, their language, their own profile, password and email choices, feedback, and their own bell
    ALLOWED = {
        "logout", "set_language", "profile", "notification_settings", "password_change", "feedback",
        "notification_read_all", "password_reset", "password_reset_confirm",
    }
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and user.is_read_only
                and request.method not in self.SAFE_METHODS and not self.allowed(request)):
            raise PermissionDenied(translation.gettext("Your role can only read: nothing was changed."))
        return self.get_response(request)

    def allowed(self, request):
        try:
            return resolve(request.path_info).view_name in self.ALLOWED
        except Resolver404:
            return False
