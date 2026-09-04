"""Tests for the Supabase Auth Admin client.

The header rules differ between the two key formats, and getting them wrong
fails at runtime against the live API rather than at import, so they are
pinned here.
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from core.supabase_admin import (
    SupabaseAdminError,
    SupabaseAdminNotConfigured,
    _headers,
    create_user,
    find_user_by_email,
    invite_user,
    is_configured,
    is_new_format,
)

NEW_KEY = "sb_secret_abc123def456"
LEGACY_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.legacy.signature"


class KeyFormatTests(SimpleTestCase):
    def test_recognises_new_format(self):
        self.assertTrue(is_new_format(NEW_KEY))
        self.assertTrue(is_new_format("sb_publishable_xyz"))

    def test_recognises_legacy_jwt(self):
        self.assertFalse(is_new_format(LEGACY_KEY))


class HeaderTests(SimpleTestCase):
    @override_settings(SUPABASE_SECRET_KEY=NEW_KEY)
    def test_new_secret_key_goes_on_apikey_only(self):
        """Supabase: send secret keys on `apikey`, not `Authorization: Bearer`.
        They are not JWTs, so anything verifying one as a JWT fails."""
        headers = _headers()
        self.assertEqual(headers["apikey"], NEW_KEY)
        self.assertNotIn("Authorization", headers)

    @override_settings(SUPABASE_SECRET_KEY=LEGACY_KEY)
    def test_legacy_service_role_key_still_sends_bearer(self):
        """The legacy key's admin rights come from the `role` claim in the
        JWT, which GoTrue reads off the Bearer token."""
        headers = _headers()
        self.assertEqual(headers["apikey"], LEGACY_KEY)
        self.assertEqual(headers["Authorization"], f"Bearer {LEGACY_KEY}")

    @override_settings(SUPABASE_SECRET_KEY="")
    def test_missing_key_raises_clearly(self):
        with self.assertRaises(SupabaseAdminNotConfigured):
            _headers()
        self.assertFalse(is_configured())


@override_settings(
    SUPABASE_SECRET_KEY=NEW_KEY,
    SUPABASE_URL="https://project.supabase.co",
    SUPABASE_INVITE_REDIRECT_URL="https://app.example.com/auth/callback",
)
class InviteUserTests(SimpleTestCase):
    @mock.patch("core.supabase_admin.requests.post")
    def test_posts_to_invite_endpoint_with_redirect(self, post):
        post.return_value = mock.Mock(
            status_code=200, json=mock.Mock(return_value={"id": "user-uuid"})
        )
        user_id = invite_user("nurse@example.com")

        self.assertEqual(user_id, "user-uuid")
        url = post.call_args.args[0]
        self.assertEqual(url, "https://project.supabase.co/auth/v1/invite")
        self.assertEqual(post.call_args.kwargs["json"]["email"], "nurse@example.com")
        self.assertEqual(
            post.call_args.kwargs["json"]["redirect_to"],
            "https://app.example.com/auth/callback",
        )

    @mock.patch("core.supabase_admin.requests.post")
    def test_new_key_is_not_sent_as_bearer_on_the_wire(self, post):
        """The regression that matters: a Bearer header here would make
        GoTrue try to parse an opaque key as a JWT and reject the call."""
        post.return_value = mock.Mock(
            status_code=200, json=mock.Mock(return_value={"id": "u"})
        )
        invite_user("nurse@example.com")
        sent = post.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", sent)
        self.assertEqual(sent["apikey"], NEW_KEY)

    @mock.patch("core.supabase_admin.requests.post")
    def test_error_status_raises_without_leaking_the_key(self, post):
        post.return_value = mock.Mock(
            status_code=401,
            json=mock.Mock(return_value={"msg": "invalid api key"}),
        )
        with self.assertRaises(SupabaseAdminError) as ctx:
            invite_user("nurse@example.com")
        message = str(ctx.exception)
        self.assertIn("401", message)
        self.assertNotIn(NEW_KEY, message)

    @mock.patch("core.supabase_admin.requests.post")
    def test_network_failure_is_wrapped(self, post):
        import requests

        post.side_effect = requests.RequestException("connection reset")
        with self.assertRaises(SupabaseAdminError):
            invite_user("nurse@example.com")


@override_settings(
    SUPABASE_SECRET_KEY=NEW_KEY,
    SUPABASE_URL="https://project.supabase.co",
)
class CreateUserTests(SimpleTestCase):
    """Direct provisioning: the path that sends no email at all."""

    @mock.patch("core.supabase_admin.requests.post")
    def test_creates_a_confirmed_account_without_an_email(self, post):
        post.return_value = mock.Mock(
            status_code=200, json=mock.Mock(return_value={"id": "user-uuid"})
        )
        user_id = create_user("nurse@example.com", "s3cret")

        self.assertEqual(user_id, "user-uuid")
        self.assertEqual(
            post.call_args.args[0],
            "https://project.supabase.co/auth/v1/admin/users",
        )
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["email"], "nurse@example.com")
        self.assertEqual(body["password"], "s3cret")
        # Without this the account exists but is stuck behind a confirmation
        # link that was never sent, so nobody can sign in.
        self.assertIs(body["email_confirm"], True)

    @mock.patch("core.supabase_admin.requests.post")
    def test_does_not_touch_the_invite_endpoint(self, post):
        """Avoiding the rate-limited SMTP path is the entire point."""
        post.return_value = mock.Mock(
            status_code=200, json=mock.Mock(return_value={"id": "u"})
        )
        create_user("nurse@example.com", "pw")
        self.assertNotIn("/invite", post.call_args.args[0])

    @mock.patch("core.supabase_admin.requests.post")
    def test_error_does_not_leak_the_key_or_the_password(self, post):
        post.return_value = mock.Mock(
            status_code=422,
            json=mock.Mock(return_value={"msg": "email already registered"}),
        )
        with self.assertRaises(SupabaseAdminError) as ctx:
            create_user("nurse@example.com", "hunter2")
        message = str(ctx.exception)
        self.assertIn("422", message)
        self.assertNotIn(NEW_KEY, message)
        self.assertNotIn("hunter2", message)


@override_settings(
    SUPABASE_SECRET_KEY=NEW_KEY,
    SUPABASE_URL="https://project.supabase.co",
)
class FindUserByEmailTests(SimpleTestCase):
    @staticmethod
    def page(*emails):
        return mock.Mock(
            status_code=200,
            json=mock.Mock(
                return_value={
                    "users": [{"id": f"id-{e}", "email": e} for e in emails]
                }
            ),
        )

    @mock.patch("core.supabase_admin.requests.get")
    def test_finds_a_user_on_the_first_page(self, get):
        get.return_value = self.page("a@example.com", "b@example.com")
        self.assertEqual(find_user_by_email("b@example.com"), "id-b@example.com")

    @mock.patch("core.supabase_admin.requests.get")
    def test_matching_ignores_case(self, get):
        get.return_value = self.page("Nurse@Example.com")
        self.assertEqual(
            find_user_by_email("nurse@example.com"), "id-Nurse@Example.com"
        )

    @mock.patch("core.supabase_admin.requests.get")
    def test_returns_none_when_absent(self, get):
        get.return_value = self.page("someone@example.com")
        self.assertIsNone(find_user_by_email("nobody@example.com"))

    @mock.patch("core.supabase_admin.requests.get")
    def test_stops_paging_on_a_short_page(self, get):
        """A short page means the end of the list. Paging on would loop over
        the same empty result up to the hard stop."""
        get.return_value = self.page("only@example.com")
        find_user_by_email("nobody@example.com")
        self.assertEqual(get.call_count, 1)

    @mock.patch("core.supabase_admin.requests.get")
    def test_walks_past_a_full_page(self, get):
        full = self.page(*[f"user{n}@example.com" for n in range(200)])
        get.side_effect = [full, self.page("wanted@example.com")]
        self.assertEqual(
            find_user_by_email("wanted@example.com"), "id-wanted@example.com"
        )
        self.assertEqual(get.call_count, 2)

    @mock.patch("core.supabase_admin.requests.get")
    def test_blank_email_never_queries(self, get):
        self.assertIsNone(find_user_by_email(""))
        self.assertIsNone(find_user_by_email(None))
        get.assert_not_called()
