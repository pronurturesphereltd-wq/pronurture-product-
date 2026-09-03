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
    "core",
    "facilities",
    "profiles",
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
