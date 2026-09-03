"""Tests for Supabase JWT authentication.

These exercise the verification logic against a locally generated EC key rather
than the live Supabase key, so they are deterministic and run offline. The one
test that touches the network is skipped if the JWKS endpoint is unreachable.
"""

import datetime as dt
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from core.authentication import SupabaseJWTAuthentication

ISSUER = "https://testproject.supabase.co/auth/v1"
AUDIENCE = "authenticated"
SUBJECT = "6f1d2c3b-0000-4a5b-8c7d-9e0f1a2b3c4d"

_private_key = ec.generate_private_key(ec.SECP256R1())
_private_pem = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_public_pem = (
    _private_key.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)


def make_token(algorithm="ES256", key=None, **overrides):
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "sub": SUBJECT,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "email": "nurse@example.com",
        "iat": now,
        "exp": now + dt.timedelta(hours=1),
        "role": "authenticated",
    }
    claims.update(overrides)
    for empty in [k for k, v in claims.items() if v is None]:
        del claims[empty]
    if key is None:
        key = _private_pem
    return jwt.encode(claims, key, algorithm=algorithm)


class StubSigningKey:
    key = _public_pem


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class SupabaseJWTAuthenticationTests(SimpleTestCase):
    def setUp(self):
        self.auth = SupabaseJWTAuthentication()
        self.factory = APIRequestFactory()
        patcher = mock.patch(
            "core.authentication.get_jwk_client",
            return_value=mock.Mock(
                get_signing_key_from_jwt=mock.Mock(return_value=StubSigningKey())
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def request_with(self, header_value):
        return self.factory.get("/", HTTP_AUTHORIZATION=header_value)

    # --- the happy path -------------------------------------------------

    def test_valid_token_authenticates(self):
        request = self.request_with(f"Bearer {make_token()}")
        user, claims = self.auth.authenticate(request)
        self.assertTrue(user.is_authenticated)
        self.assertEqual(user.supabase_user_id, SUBJECT)
        self.assertEqual(user.email, "nurse@example.com")
        self.assertEqual(claims["role"], "authenticated")
        self.assertEqual(request.supabase_user_id, SUBJECT)

    def test_supabase_user_has_no_django_powers(self):
        request = self.request_with(f"Bearer {make_token()}")
        user, _ = self.auth.authenticate(request)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_perm("facilities.add_facility"))
        self.assertFalse(user.has_module_perms("facilities"))

    # --- absent or malformed credentials --------------------------------

    def test_no_header_returns_none(self):
        self.assertIsNone(self.auth.authenticate(self.factory.get("/")))

    def test_non_bearer_scheme_returns_none(self):
        self.assertIsNone(self.auth.authenticate(self.request_with("Basic abc123")))

    def test_bearer_with_no_token_fails(self):
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with("Bearer"))

    def test_bearer_with_spaces_fails(self):
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with("Bearer a b"))

    def test_garbage_token_fails(self):
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with("Bearer not-a-jwt"))

    def test_401_challenge_header(self):
        self.assertEqual(self.auth.authenticate_header(self.factory.get("/")), "Bearer")

    # --- signature and algorithm attacks --------------------------------

    def test_token_signed_by_wrong_key_is_rejected(self):
        attacker_key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        token = make_token(key=attacker_key)
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with(f"Bearer {token}"))

    def test_unsigned_alg_none_token_is_rejected(self):
        token = jwt.encode(
            {
                "sub": SUBJECT,
                "aud": AUDIENCE,
                "iss": ISSUER,
                "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            },
            key="",
            algorithm="none",
        )
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with(f"Bearer {token}"))

    def test_hs256_signed_with_public_key_is_rejected(self):
        """Classic algorithm-confusion attack: the public key is not a secret,
        so an attacker who signs HS256 with it must still be turned away.

        Forged by hand because PyJWT's encoder refuses to use a PEM key as an
        HMAC secret — a real attacker has no such scruples.
        """
        import base64
        import hashlib
        import hmac
        import json

        def b64(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        exp = int(
            (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).timestamp()
        )
        header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = b64(
            json.dumps(
                {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": exp}
            ).encode()
        )
        signing_input = header + b"." + payload
        signature = b64(
            hmac.new(_public_pem.encode(), signing_input, hashlib.sha256).digest()
        )
        token = (signing_input + b"." + signature).decode()

        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with(f"Bearer {token}"))

    # --- claim validation -----------------------------------------------

    def test_expired_token_is_rejected(self):
        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
        token = make_token(exp=past, iat=past - dt.timedelta(minutes=5))
        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(self.request_with(f"Bearer {token}"))
        self.assertIn("expired", str(ctx.exception).lower())

    def test_wrong_audience_is_rejected(self):
        token = make_token(aud="some-other-service")
        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(self.request_with(f"Bearer {token}"))
        self.assertIn("audience", str(ctx.exception).lower())

    def test_wrong_issuer_is_rejected(self):
        token = make_token(iss="https://evil.example.com/auth/v1")
        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(self.request_with(f"Bearer {token}"))
        self.assertIn("issuer", str(ctx.exception).lower())

    def test_token_without_exp_is_rejected(self):
        token = make_token(exp=None)
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with(f"Bearer {token}"))

    def test_token_without_sub_is_rejected(self):
        token = make_token(sub=None)
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request_with(f"Bearer {token}"))


class LiveJWKSTests(SimpleTestCase):
    """Confirms the configured project really publishes a usable signing key."""

    def test_project_jwks_serves_a_verification_key(self):
        import urllib.error

        from django.conf import settings

        from core.authentication import get_jwk_client

        try:
            keys = get_jwk_client().get_jwk_set().keys
        except (urllib.error.URLError, OSError) as exc:
            self.skipTest(f"JWKS endpoint unreachable: {exc}")

        self.assertTrue(keys, f"No keys published at {settings.SUPABASE_JWKS_URL}")
        self.assertTrue(
            any(k._jwk_data.get("alg") in ("ES256", "RS256") for k in keys),
            "No ES256/RS256 signing key published",
        )
