"""Supabase JWT authentication for the public API.

Django never issues these tokens. Supabase Auth signs them with an asymmetric
key (this project uses ES256) and we verify the signature against the public
keys published at the project's JWKS endpoint.

The authenticated party is therefore not a Django user and must never be
confused with one: Django users are PSL staff who log into the admin. So this
class returns a `SupabaseUser`, which deliberately has no database row, no
permissions, and no admin access.
"""

import threading

import jwt
from django.conf import settings
from jwt import PyJWKClient
from rest_framework import authentication, exceptions

# Algorithms we accept. Pinning these is what stops an attacker presenting a
# token with `alg: none`, or an HS256 token signed with the public key as if it
# were a shared secret.
ALLOWED_ALGORITHMS = ["ES256", "RS256"]

_jwk_client = None
_jwk_client_lock = threading.Lock()


def get_jwk_client():
    """One cached PyJWKClient per process.

    PyJWKClient caches the fetched key set, so building it once keeps the JWKS
    endpoint out of the path of every request while still allowing the client
    to refetch when it sees an unknown key id after a key rotation.
    """
    global _jwk_client
    if _jwk_client is None:
        with _jwk_client_lock:
            if _jwk_client is None:
                _jwk_client = PyJWKClient(
                    settings.SUPABASE_JWKS_URL,
                    cache_keys=True,
                    lifespan=settings.SUPABASE_JWKS_CACHE_SECONDS,
                )
    return _jwk_client


class SupabaseUser:
    """A verified Supabase identity, not a Django user.

    Implements just enough of the Django user contract for DRF permission
    classes such as IsAuthenticated to work.
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, supabase_user_id, email=None, claims=None):
        self.supabase_user_id = supabase_user_id
        self.email = email
        self.claims = claims or {}

    @property
    def pk(self):
        return self.supabase_user_id

    def __str__(self):
        return f"SupabaseUser({self.supabase_user_id})"

    def has_perm(self, perm, obj=None):
        return False

    def has_module_perms(self, app_label):
        return False


class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    """Verify `Authorization: Bearer <token>` against the Supabase JWKS."""

    keyword = "Bearer"

    def authenticate(self, request):
        token = self.get_token(request)
        if token is None:
            # No credentials offered — let other authenticators (or the
            # permission layer) decide. Returning None is not a failure.
            return None

        claims = self.decode(token)

        supabase_user_id = claims.get("sub")
        if not supabase_user_id:
            raise exceptions.AuthenticationFailed("Token is missing the 'sub' claim.")

        user = SupabaseUser(
            supabase_user_id=supabase_user_id,
            email=claims.get("email"),
            claims=claims,
        )
        # Convenience for views, per the project spec.
        request.supabase_user_id = supabase_user_id
        return (user, claims)

    def get_token(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header:
            return None
        if header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) == 1:
            raise exceptions.AuthenticationFailed(
                "Invalid Authorization header: no credentials provided."
            )
        if len(header) > 2:
            raise exceptions.AuthenticationFailed(
                "Invalid Authorization header: token must not contain spaces."
            )
        try:
            return header[1].decode()
        except UnicodeError:
            raise exceptions.AuthenticationFailed(
                "Invalid Authorization header: token is not valid UTF-8."
            )

    def decode(self, token):
        try:
            signing_key = get_jwk_client().get_signing_key_from_jwt(token)
        except jwt.exceptions.PyJWKClientError as exc:
            raise exceptions.AuthenticationFailed(f"Cannot verify token: {exc}")
        except jwt.exceptions.DecodeError:
            raise exceptions.AuthenticationFailed("Token is malformed.")

        try:
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=settings.SUPABASE_JWT_AUDIENCE,
                issuer=settings.SUPABASE_JWT_ISSUER,
                leeway=settings.SUPABASE_JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("Token has expired.")
        except jwt.InvalidAudienceError:
            raise exceptions.AuthenticationFailed("Token has an invalid audience.")
        except jwt.InvalidIssuerError:
            raise exceptions.AuthenticationFailed("Token has an invalid issuer.")
        except jwt.InvalidTokenError as exc:
            raise exceptions.AuthenticationFailed(f"Token is invalid: {exc}")

    def authenticate_header(self, request):
        # Makes DRF answer 401 rather than 403 when credentials are absent.
        return self.keyword
