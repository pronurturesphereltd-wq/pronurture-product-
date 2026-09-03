from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from profiles.models import Profile

SUPABASE_ID = "aaaaaaaa-2222-4333-8444-555555555555"


class SupabaseAuthMixin:
    def setUp(self):
        super().setUp()
        patcher = mock.patch(
            "core.authentication.get_jwk_client",
            return_value=mock.Mock(
                get_signing_key_from_jwt=mock.Mock(return_value=StubSigningKey())
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def authenticate(self, sub=SUPABASE_ID):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(sub=sub)}")


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class ProfileSeedBulkAPITests(SupabaseAuthMixin, APITestCase):
    url = "/api/profiles/seed-bulk/"

    def rows(self, count=2):
        return [
            {
                "full_name": f"Nurse {i}",
                "email": f"nurse{i}@example.com",
                "phone": "07700900000",
                "license_number": f"NMC-{i:04d}",
                "license_body": "NMC",
            }
            for i in range(count)
        ]

    def test_requires_authentication(self):
        response = self.client.post(self.url, self.rows(), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(Profile.objects.count(), 0)

    def test_creates_rows_as_bulk_import_pending(self):
        self.authenticate()
        response = self.client.post(self.url, self.rows(3), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Profile.objects.count(), 3)
        for profile in Profile.objects.all():
            self.assertEqual(profile.onboarding_path, Profile.OnboardingPath.BULK_IMPORT)
            self.assertEqual(profile.verification_state, Profile.VerificationState.PENDING)
            self.assertEqual(profile.history.count(), 1)

    def test_non_array_body_rejected(self):
        self.authenticate()
        response = self.client.post(self.url, self.rows(1)[0], format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Profile.objects.count(), 0)

    def test_import_is_all_or_nothing(self):
        """A duplicate email in row two must not leave row one behind."""
        self.authenticate()
        rows = self.rows(2)
        rows[1]["email"] = rows[0]["email"]
        response = self.client.post(self.url, rows, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Profile.objects.count(), 0)


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class ProfileSelfRegisterAPITests(SupabaseAuthMixin, APITestCase):
    url = "/api/profiles/self-register/"

    def payload(self, **overrides):
        data = {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "07700900001",
            "license_number": "NMC-9911",
            "license_body": "NMC",
        }
        data.update(overrides)
        return data

    def test_requires_authentication(self):
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creates_self_registered_unverified_profile(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        profile = Profile.objects.get()
        self.assertEqual(
            profile.verification_state,
            Profile.VerificationState.SELF_REGISTERED_UNVERIFIED,
        )
        self.assertEqual(profile.onboarding_path, Profile.OnboardingPath.INVITE_LINK)
        self.assertEqual(str(profile.supabase_user_id), SUPABASE_ID)
        self.assertIsNone(profile.verified_at)
        self.assertIsNone(profile.verified_by)

    def test_cannot_self_verify_via_payload(self):
        self.authenticate()
        response = self.client.post(
            self.url, self.payload(verification_state="verified"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Profile.objects.get().verification_state,
            Profile.VerificationState.SELF_REGISTERED_UNVERIFIED,
        )

    def test_duplicate_registration_for_same_account_conflicts(self):
        self.authenticate()
        self.client.post(self.url, self.payload(), format="json")
        response = self.client.post(
            self.url, self.payload(email="jane2@example.com"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Profile.objects.count(), 1)


class ProfileAdminActionTests(TestCase):
    """Definition-of-done item 5: verify/reject, recorded in history.

    Runs as a real `verification_officer`, through the admin URL, so the
    middleware supplies the acting user to simple_history.
    """

    def setUp(self):
        self.officer = User.objects.create_user(
            username="officer", password="x", is_staff=True
        )
        self.officer.groups.add(Group.objects.get(name="verification_officer"))
        self.client.force_login(self.officer)
        self.url = reverse("admin:profiles_profile_changelist")
        self.profile = Profile.objects.create(
            full_name="Jane Doe",
            email="jane@example.com",
            license_number="NMC-1",
            license_body="NMC",
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    def run_action(self, action):
        return self.client.post(
            self.url,
            {"action": action, "_selected_action": [str(self.profile.pk)]},
            follow=True,
        )

    def test_verify_sets_state_and_verifier(self):
        response = self.run_action("verify_profiles")
        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get()
        self.assertEqual(profile.verification_state, Profile.VerificationState.VERIFIED)
        self.assertEqual(profile.verified_by, self.officer)
        self.assertIsNotNone(profile.verified_at)

    def test_reject_sets_state(self):
        self.run_action("reject_profiles")
        self.assertEqual(
            Profile.objects.get().verification_state,
            Profile.VerificationState.REJECTED,
        )

    def test_verification_is_recorded_in_history(self):
        self.run_action("verify_profiles")
        profile = Profile.objects.get()
        self.assertEqual(profile.history.count(), 2)
        latest = profile.history.first()
        self.assertEqual(latest.history_type, "~")
        self.assertEqual(latest.history_user, self.officer)
        states = [h.verification_state for h in profile.history.order_by("history_date")]
        self.assertEqual(
            states,
            [Profile.VerificationState.PENDING, Profile.VerificationState.VERIFIED],
        )


class RBACGroupTests(TestCase):
    """Definition-of-done item 6, at the permission level.

    The migration that creates these groups runs against the test database too,
    so this also proves the migration works on a fresh database.
    """

    def test_groups_exist_with_expected_scope(self):
        admin_group = Group.objects.get(name="admin")
        officer_group = Group.objects.get(name="verification_officer")

        admin_apps = {p.content_type.app_label for p in admin_group.permissions.all()}
        officer_apps = {
            p.content_type.app_label for p in officer_group.permissions.all()
        }

        self.assertEqual(admin_apps, {"facilities", "profiles"})
        self.assertEqual(officer_apps, {"profiles"})

    def test_verification_officer_cannot_touch_facilities(self):
        officer = User.objects.create_user(
            username="officer2", password="x", is_staff=True
        )
        officer.groups.add(Group.objects.get(name="verification_officer"))
        officer = User.objects.get(pk=officer.pk)  # drop the permission cache

        self.assertFalse(officer.has_module_perms("facilities"))
        self.assertTrue(officer.has_module_perms("profiles"))
        self.assertFalse(officer.has_perm("facilities.view_facility"))
        self.assertTrue(officer.has_perm("profiles.change_profile"))
