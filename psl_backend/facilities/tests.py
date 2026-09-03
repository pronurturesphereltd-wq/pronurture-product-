from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility

SUPABASE_ID = "11111111-2222-4333-8444-555555555555"


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class FacilityRegisterAPITests(APITestCase):
    url = "/api/facilities/register/"

    def setUp(self):
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

    def payload(self, **overrides):
        data = {
            "name": "Oakwood Care Home",
            "registration_number": "REG-001",
            "contact_email": "hello@oakwood.example",
        }
        data.update(overrides)
        return data

    def test_requires_authentication(self):
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(Facility.objects.count(), 0)

    def test_rejects_invalid_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer garbage.token.here")
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creates_pending_facility_with_supabase_id(self):
        self.authenticate()
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        facility = Facility.objects.get()
        self.assertEqual(facility.status, Facility.Status.PENDING)
        self.assertEqual(str(facility.supabase_user_id), SUPABASE_ID)
        self.assertIsNone(facility.approved_at)
        self.assertIsNone(facility.approved_by)

    def test_registration_is_recorded_in_history(self):
        self.authenticate()
        self.client.post(self.url, self.payload(), format="json")
        facility = Facility.objects.get()
        self.assertEqual(facility.history.count(), 1)
        self.assertEqual(facility.history.first().history_type, "+")

    def test_cannot_self_approve_via_payload(self):
        """status is read-only — a registrant must not be able to walk in
        already approved."""
        self.authenticate()
        response = self.client.post(
            self.url, self.payload(status="approved"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Facility.objects.get().status, Facility.Status.PENDING)

    def test_duplicate_registration_for_same_account_conflicts(self):
        self.authenticate()
        self.client.post(self.url, self.payload(), format="json")
        response = self.client.post(
            self.url,
            self.payload(contact_email="second@oakwood.example"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Facility.objects.count(), 1)

    def test_duplicate_contact_email_is_rejected(self):
        self.authenticate()
        self.client.post(self.url, self.payload(), format="json")
        self.authenticate(sub="99999999-2222-4333-8444-555555555555")
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("contact_email", response.data)

    def test_missing_required_fields_rejected(self):
        self.authenticate()
        response = self.client.post(self.url, {"name": "Only a name"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("contact_email", response.data)


class FacilityAdminActionTests(TestCase):
    """Definition-of-done item 3: bulk approve/reject, recorded in history.

    Driven through the real admin URL rather than by calling the action
    directly, because HistoryRequestMiddleware is what tells simple_history who
    is acting. Calling the action in isolation skips it and records no user.
    """

    def setUp(self):
        self.staff = User.objects.create_superuser(
            username="staffer", password="x", email="s@example.com"
        )
        self.client.force_login(self.staff)
        self.url = reverse("admin:facilities_facility_changelist")
        self.facilities = [
            Facility.objects.create(
                name=f"Home {i}",
                registration_number=f"REG-{i}",
                contact_email=f"home{i}@example.com",
            )
            for i in range(2)
        ]

    def run_action(self, action):
        return self.client.post(
            self.url,
            {
                "action": action,
                "_selected_action": [str(f.pk) for f in self.facilities],
            },
            follow=True,
        )

    def test_approve_sets_status_and_approver(self):
        response = self.run_action("approve_facilities")
        self.assertEqual(response.status_code, 200)
        for facility in Facility.objects.all():
            self.assertEqual(facility.status, Facility.Status.APPROVED)
            self.assertEqual(facility.approved_by, self.staff)
            self.assertIsNotNone(facility.approved_at)

    def test_reject_sets_status(self):
        self.run_action("reject_facilities")
        for facility in Facility.objects.all():
            self.assertEqual(facility.status, Facility.Status.REJECTED)

    def test_approval_is_recorded_in_history_with_the_acting_user(self):
        self.run_action("approve_facilities")
        facility = Facility.objects.first()
        # One record for creation, one for the approval.
        self.assertEqual(facility.history.count(), 2)
        latest = facility.history.first()
        self.assertEqual(latest.history_type, "~")
        self.assertEqual(latest.status, Facility.Status.APPROVED)
        self.assertEqual(latest.history_user, self.staff)

    def test_history_preserves_the_previous_status(self):
        """The point of the audit trail: the earlier value survives."""
        self.run_action("approve_facilities")
        facility = Facility.objects.first()
        statuses = [h.status for h in facility.history.order_by("history_date")]
        self.assertEqual(
            statuses, [Facility.Status.PENDING, Facility.Status.APPROVED]
        )
