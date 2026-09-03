"""Thin client for the Supabase Auth Admin API.

Used by bulk import to provision an account per imported professional. The
invite endpoint both creates the auth user and sends the login email, so one
call covers steps (c) and (d) of the import.

This uses the service-role key, which bypasses row-level security. It is read
from the environment, is never sent to a client, and must not be logged.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30


class SupabaseAdminError(Exception):
    """A call to the Auth Admin API did not succeed."""


class SupabaseAdminNotConfigured(SupabaseAdminError):
    """No service-role key is set, so no account can be provisioned."""


def is_configured():
    return bool(settings.SUPABASE_SECRET_KEY)


def is_new_format(key):
    """New-format keys are opaque strings, not JWTs: sb_secret_... /
    sb_publishable_.... Legacy anon/service_role keys are JWTs starting 'ey'."""
    return key.startswith("sb_")


def _headers():
    key = settings.SUPABASE_SECRET_KEY
    if not key:
        raise SupabaseAdminNotConfigured(
            "SUPABASE_SECRET_KEY is not set; cannot provision accounts."
        )

    headers = {"apikey": key, "Content-Type": "application/json"}

    if not is_new_format(key):
        # Legacy service_role key: a JWT whose `role` claim is what grants
        # admin rights, and GoTrue reads that from the Bearer token.
        headers["Authorization"] = f"Bearer {key}"
    # New-format secret keys go on `apikey` only. Supabase is explicit that
    # they must not be sent as Bearer: they are not JWTs, so anything trying
    # to verify one as a JWT fails. Sending both would break the call.

    return headers


def invite_user(email, redirect_to=None):
    """Create an auth account for `email` and send them a login email.

    Returns the Supabase user id. Raises SupabaseAdminError on failure — the
    caller decides whether that is fatal for the whole import or just this row.
    """
    url = f"{settings.SUPABASE_URL}/auth/v1/invite"
    payload = {"email": email}
    redirect_to = redirect_to or settings.SUPABASE_INVITE_REDIRECT_URL
    if redirect_to:
        payload["redirect_to"] = redirect_to

    try:
        response = requests.post(
            url, json=payload, headers=_headers(), timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise SupabaseAdminError(f"Could not reach Supabase Auth: {exc}") from exc

    if response.status_code >= 400:
        # Deliberately not logging the response body at error level — it echoes
        # the email address, and this runs over whole staff lists.
        raise SupabaseAdminError(
            f"Supabase Auth returned {response.status_code} for invite "
            f"({_safe_detail(response)})"
        )

    try:
        return response.json().get("id")
    except ValueError:
        raise SupabaseAdminError("Supabase Auth returned a non-JSON response.")


def _safe_detail(response):
    try:
        data = response.json()
    except ValueError:
        return "unparseable response"
    for key in ("msg", "message", "error_description", "error"):
        if key in data:
            return str(data[key])[:200]
    return "no detail"
