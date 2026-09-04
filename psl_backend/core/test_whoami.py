"""/api/me/ — the endpoint that lets the frontend know who it is talking to."""

from unittest import mock

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility
from profiles.models import Profile

FACILITY_SUB = "f0000000-0000-4000-8000-000000000010"
PENDING_FACILITY_SUB = "f0000000-0000-4000-8000-000000000011"
PROFESSIONAL_SUB = "aaaaaaaa-0000-4000-8000-000000000001"
NOMAD_SUB = "99999999-0000-4000-8000-000000000099"
STRANGER_SUB = "88888888-0000-4000-8000-000000000088"

URL = "/api/me/"


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class WhoAmITests(APITestCase):
    def setUp(self):
        patcher = mock.patch(
            "core.authentication.get_jwk_client",
            return_value=mock.Mock(
                get_signing_key_from_jwt=mock.Mock(return_value=StubSigningKey())
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            supabase_user_id=FACILITY_SUB,
            status=Facility.Status.APPROVED,
        )
        self.profile = Profile.objects.create(
            full_name="Amaka Nurse",
            email="amaka@example.com",
            license_number="NMC-1",
            license_body="NMC",
            role="A&E Nurse",
            facility=self.facility,
            supabase_user_id=PROFESSIONAL_SUB,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    def authenticate(self, sub):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(sub=sub)}")

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.get(URL).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_facility_identifies_as_a_facility(self):
        self.authenticate(FACILITY_SUB)
        data = self.client.get(URL).data

        self.assertEqual(data["kind"], "facility")
        self.assertEqual(data["facility"]["name"], "Oakwood")
        self.assertIsNone(data["profile"])

    def test_professional_identifies_as_a_professional(self):
        self.authenticate(PROFESSIONAL_SUB)
        data = self.client.get(URL).data

        self.assertEqual(data["kind"], "professional")
        self.assertEqual(data["profile"]["full_name"], "Amaka Nurse")
        # Surfaced so a professional refused a swap can see why.
        self.assertEqual(data["profile"]["role"], "A&E Nurse")
        self.assertEqual(data["facility"]["name"], "Oakwood")

    def test_professional_without_a_facility_still_resolves(self):
        Profile.objects.create(
            full_name="Nomad",
            email="nomad@example.com",
            license_number="NMC-N",
            license_body="NMC",
            supabase_user_id=NOMAD_SUB,
            onboarding_path=Profile.OnboardingPath.INVITE_LINK,
        )
        self.authenticate(NOMAD_SUB)
        data = self.client.get(URL).data

        self.assertEqual(data["kind"], "professional")
        self.assertIsNone(data["facility"])

    def test_unknown_account_is_told_it_has_no_record(self):
        """A valid token with no PSL record is a real state — someone who
        signed up and got no further. The frontend needs to tell them that
        rather than showing an empty dashboard."""
        self.authenticate(STRANGER_SUB)
        response = self.client.get(URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("No facility or professional", response.data["detail"])

    def test_unapproved_facility_hears_about_its_status(self):
        """The more useful message of the two: this account is known, it is
        just not approved yet."""
        Facility.objects.create(
            name="Pending Place",
            registration_number="REG-2",
            contact_email="pending@example.com",
            supabase_user_id=PENDING_FACILITY_SUB,
            status=Facility.Status.PENDING,
        )
        self.authenticate(PENDING_FACILITY_SUB)
        response = self.client.get(URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("pending", response.data["detail"])

    def test_does_not_leak_the_other_side(self):
        """A facility learns nothing about individual professionals here, and
        a professional learns nothing about the roster."""
        self.authenticate(FACILITY_SUB)
        self.assertIsNone(self.client.get(URL).data["profile"])
