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


def create_user(email, password, user_metadata=None):
    """Create an already-confirmed auth account, sending no email at all.

    `invite_user` is the normal onboarding path, but it goes through Supabase's
    default SMTP, which is rate limited — three rows was enough to earn a 429.
    Seeding an account for someone whose profile already exists does not need
    an email round trip, and this avoids spending the quota on one.

    `email_confirm: true` marks the address confirmed at creation, so the
    person can sign in with the password immediately rather than being stuck
    behind a confirmation link that was never sent.

    Returns the Supabase user id.
    """
    url = f"{settings.SUPABASE_URL}/auth/v1/admin/users"
    payload = {"email": email, "password": password, "email_confirm": True}
    if user_metadata:
        payload["user_metadata"] = user_metadata

    try:
        response = requests.post(
            url, json=payload, headers=_headers(), timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise SupabaseAdminError(f"Could not reach Supabase Auth: {exc}") from exc

    if response.status_code >= 400:
        raise SupabaseAdminError(
            f"Supabase Auth returned {response.status_code} creating the user "
            f"({_safe_detail(response)})"
        )

    try:
        return response.json().get("id")
    except ValueError:
        raise SupabaseAdminError("Supabase Auth returned a non-JSON response.")


def find_user_by_email(email):
    """Return the Supabase user id for `email`, or None.

    Pages the admin list rather than trusting a server-side filter, because the
    `filter` parameter's behaviour varies by GoTrue version and a silently
    ignored filter would return page one and look like a match.
    """
    wanted = (email or "").strip().lower()
    if not wanted:
        return None

    url = f"{settings.SUPABASE_URL}/auth/v1/admin/users"
    per_page = 200
    for page in range(1, 51):  # a hard stop rather than a while True
        try:
            response = requests.get(
                url,
                params={"page": page, "per_page": per_page},
                headers=_headers(),
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SupabaseAdminError(f"Could not reach Supabase Auth: {exc}") from exc

        if response.status_code >= 400:
            raise SupabaseAdminError(
                f"Supabase Auth returned {response.status_code} listing users "
                f"({_safe_detail(response)})"
            )

        try:
            users = response.json().get("users", [])
        except ValueError:
            raise SupabaseAdminError("Supabase Auth returned a non-JSON response.")

        for user in users:
            if (user.get("email") or "").strip().lower() == wanted:
                return user.get("id")
        if len(users) < per_page:
            return None
    return None


def _safe_detail(response):
    try:
        data = response.json()
    except ValueError:
        return "unparseable response"
    for key in ("msg", "message", "error_description", "error"):
        if key in data:
            return str(data[key])[:200]
    return "no detail"
