"""
Django settings for psl_backend project.

Secrets and environment-specific values are read from a .env file next to
manage.py. See .env.example for the required keys.
"""

import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise ImproperlyConfigured(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)

DEBUG = env("DJANGO_DEBUG", "False").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = [
    host.strip()
    for host in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

# Supabase Auth — used by the JWT authentication class in core/.
# Tokens are signed asymmetrically (ES256) under Supabase's JWT Signing Keys
# system and verified against the project's public JWKS endpoint. There is no
# shared secret: nothing here is confidential.
SUPABASE_URL = env("SUPABASE_URL", required=True).rstrip("/")
SUPABASE_JWKS_URL = env(
    "SUPABASE_JWKS_URL",
    f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
)
SUPABASE_JWT_ISSUER = env("SUPABASE_JWT_ISSUER", f"{SUPABASE_URL}/auth/v1")
SUPABASE_JWT_AUDIENCE = env("SUPABASE_JWT_AUDIENCE", "authenticated")
SUPABASE_JWT_LEEWAY_SECONDS = int(env("SUPABASE_JWT_LEEWAY_SECONDS", "10"))
SUPABASE_JWKS_CACHE_SECONDS = int(env("SUPABASE_JWKS_CACHE_SECONDS", "300"))

# Supabase Auth Admin API — provisions accounts during bulk import. This key
# bypasses row-level security and must never reach the frontend or version
# control. Absent, bulk import still creates Profile rows but provisions no
# accounts, and says so in the import report rather than failing silently.
#
# Prefer a new-format secret key (sb_secret_...). The legacy service_role JWT
# is read as a fallback so an existing deployment keeps working, but Supabase
# is retiring those. The two formats need different headers — see
# core/supabase_admin.py.
SUPABASE_SECRET_KEY = env("SUPABASE_SECRET_KEY", "") or env(
    "SUPABASE_SERVICE_ROLE_KEY", ""
)
SUPABASE_AUTH_ADMIN_URL = f"{SUPABASE_URL}/auth/v1/admin"
# Where the invite/login email sends the professional.
SUPABASE_INVITE_REDIRECT_URL = env(
    "SUPABASE_INVITE_REDIRECT_URL", "http://localhost:3000/auth/callback"
)

# Firebase Cloud Messaging — path to the service account JSON. Absent, push
# sends are skipped with a logged warning instead of raising.
FIREBASE_CREDENTIALS_FILE = env("FIREBASE_CREDENTIALS_FILE", "")

# Shift reminders: how far ahead to warn, and how wide the sweep window is.
# The window must comfortably exceed the scheduler interval or shifts can fall
# between two runs and never be reminded at all.
SHIFT_REMINDER_LEAD_MINUTES = int(env("SHIFT_REMINDER_LEAD_MINUTES", "60"))
SHIFT_REMINDER_WINDOW_MINUTES = int(env("SHIFT_REMINDER_WINDOW_MINUTES", "20"))
SHIFT_REMINDER_INTERVAL_MINUTES = int(env("SHIFT_REMINDER_INTERVAL_MINUTES", "15"))

# Whether a facility must be approved before it can import staff, issue invite
# links, or manage a rota. Secure default; set to 0 for local testing against a
# facility still sitting in the Phase 0 pending queue.
REQUIRE_APPROVED_FACILITY = env("PSL_REQUIRE_APPROVED_FACILITY", "1") == "1"

# Bulk import guard rails.
BULK_IMPORT_MAX_BYTES = int(env("BULK_IMPORT_MAX_BYTES", str(5 * 1024 * 1024)))
BULK_IMPORT_MAX_ROWS = int(env("BULK_IMPORT_MAX_ROWS", "5000"))


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "simple_history",
    "django_q",
    "core",
    "facilities",
    "profiles",
    "rota",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "psl_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "psl_backend.wsgi.application"


# Database — Supabase Postgres

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", required=True),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# The test runner creates and drops a real database. Pointed at Supabase that
# means CREATE DATABASE against the production project, over a connection
# pooler that drops long-running sessions mid-suite. Tests therefore run on a
# local SQLite database: fast, offline, and incapable of touching production.
# Set PSL_TEST_ON_POSTGRES=1 to run the suite against DATABASE_URL instead.
RUNNING_TESTS = "test" in sys.argv
if RUNNING_TESTS and env("PSL_TEST_ON_POSTGRES", "0") != "1":
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }

if RUNNING_TESTS:
    # The admin tests log in repeatedly and PBKDF2 dominates the runtime.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Supabase JWTs only. Session auth is deliberately absent so an admin's
    # browser cookie can never authenticate a public API call.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "core.authentication.SupabaseJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# Background jobs. The ORM broker uses the Postgres database already in play,
# so there is no Redis or separate queue service to run — one process,
# `manage.py qcluster`, alongside the web server.
Q_CLUSTER = {
    "name": "psl",
    "workers": int(env("Q_WORKERS", "2")),
    "recycle": 500,
    "timeout": 600,
    # retry must exceed timeout, or a slow job is re-queued while still running.
    "retry": 900,
    "queue_limit": 50,
    "bulk": 10,
    "orm": "default",
    "save_limit": 250,
    "catch_up": False,
    # Run tasks inline during tests instead of requiring a live cluster.
    "sync": RUNNING_TESTS,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "PSL Backend API",
    "DESCRIPTION": "Facility approval and professional licence verification.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}


# Internationalization

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
