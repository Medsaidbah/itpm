"""
Settings of the IT Projects app.

Everything that is different between your PC and the server (passwords, DEBUG, the app's
address...) comes from the .env file, not from this file. .env.example lists all of them.

Django documentation: https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Read the .env file. A value already set in the terminal wins over the one in .env.
load_dotenv(BASE_DIR / ".env")


def env_list(name, default=""):
    """A .env value made of several parts: "a.com, b.com" becomes ["a.com", "b.com"]."""
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.environ.get("DEBUG", "False") == "True"

# The addresses people type to open the app, like itpm.example.mr
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")


# ---------- Application definition ----------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "accounts",
    "simple_history",
    "projects",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sends the CSS, JavaScript and fonts quickly
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.LanguageMiddleware",  # the language of each page (needs to know who is logged in)
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "axes.middleware.AxesMiddleware",  # must stay last
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",  # LANGUAGES, LANGUAGE_CODE and LANGUAGE_BIDI
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ---------- Database ----------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------- People and logging in ----------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Where to send people to log in, and where to go after logging in or out
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Protection against password guessing (django-axes): after 5 wrong passwords in a row,
# that username is locked for 1 hour, but only from that internet address (IP).
# So someone guessing from outside can't lock out a colleague in the office.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",  # checks for a lock first
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True  # a correct password forgets the earlier mistakes
AXES_LOCKOUT_TEMPLATE = "registration/locked_out.html"


# ---------- Language and time ----------

# The languages of the app (Stage 17c). Each person chooses theirs (user menu, or the login page);
# the choice is saved in their profile, so their emails are in that language too.
# The names are written in their own language, so everyone recognizes theirs.
LANGUAGE_CODE = "en"  # the language when nothing else is chosen
LANGUAGES = [
    ("en", "English"),
    ("fr", "Français"),
    ("ar", "العربية"),  # written right to left: the whole page is mirrored
]
# The translations: locale/fr/ and locale/ar/ (the .po files are the texts, the .mo files what Django reads)
LOCALE_PATHS = [BASE_DIR / "locale"]
# The login page remembers the last language chosen on this computer for a year
LANGUAGE_COOKIE_AGE = 365 * 24 * 60 * 60
LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SAMESITE = "Lax"

TIME_ZONE = "Africa/Nouakchott"
USE_I18N = True
USE_TZ = True


# ---------- Static files: CSS, JavaScript, fonts ----------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]  # our own files, like the libraries in static/vendor/
STATIC_ROOT = BASE_DIR / "staticfiles"  # where "collectstatic" gathers everything for the server
STATIC_ROOT.mkdir(exist_ok=True)  # the folder must exist, even before the first collectstatic

STORAGES = {
    # Uploaded files
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # With DEBUG off, "collectstatic" makes compressed copies with a fingerprint in their name
    # (like tabler.min.1a2b3c.css), so browsers can keep them for a long time.
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Uploaded files (they are only given out through the app, to people who are logged in)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"


# ---------- Security when the app is reached through https:// (on the server) ----------

HTTPS = os.environ.get("HTTPS", "False") == "True"
if HTTPS:
    SECURE_SSL_REDIRECT = True  # an http:// address is sent to https://
    SESSION_COOKIE_SECURE = True  # the login cookie only travels encrypted
    CSRF_COOKIE_SECURE = True
    LANGUAGE_COOKIE_SECURE = True
    CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]
    # Browsers remember to use only https for this site. Start small (1 hour);
    # when everything works, put HSTS_SECONDS=31536000 (one year) in .env.
    SECURE_HSTS_SECONDS = int(os.environ.get("HSTS_SECONDS", "3600"))

# "check --deploy" suggests HSTS for every site under your domain, and a browser preload list.
# The other sites of your domain aren't this app's business, so we leave both off.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]


# ---------- Logs ----------
# Errors are written in the terminal (on the server, the service saves them in logs\itpm-web.err.log).
# By default Django only shows server errors when DEBUG is on: this shows them always.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        # Django's own messages, written once (without this, some appeared twice)
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Errors of the pages (code 500), with their details. "Page not found" isn't written.
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}


# ---------- Email (Step 13) ----------
# While developing, emails are printed in the terminal instead of being sent.
# On the server, the real mail service's details go in .env.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_TIMEOUT = 10  # seconds: a slow mail server can't freeze a page for long
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "IT Projects <it-projects@example.com>")

# The app's address, for the links inside emails
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000")
