"""Endpoint tests for bulk-import upload, invite links, and device registration."""

from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility, InviteLink
from profiles.models import Profile, PushDevice

FACILITY_SUPABASE_ID = "cccccccc-1111-4222-8333-444444444444"
PROFESSIONAL_SUPABASE_ID = "eeeeeeee-1111-4222-8333-444444444444"

CSV = (
    b"full_name,email,license_number,license_body,phone\n"
    b"Jane Doe,jane@example.com,NMC-1,NMC,07700900000\n"
)


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

    def authenticate(self, sub):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(sub=sub)}")


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class BulkImportUploadTests(SupabaseAuthMixin, APITestCase):
    url = "/api/facilities/bulk-import/"

    def setUp(self):
        super().setUp()
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            supabase_user_id=FACILITY_SUPABASE_ID,
            status=Facility.Status.APPROVED,
        )

    def upload(self, content=CSV, name="staff.csv"):
        return self.client.post(
            self.url,
            {"file": SimpleUploadedFile(name, content, content_type="text/csv")},
            format="multipart",
        )

    def test_requires_authentication(self):
        self.assertEqual(self.upload().status_code, status.HTTP_401_UNAUTHORIZED)

    @mock.patch("facilities.views.async_task")
    def test_returns_202_immediately_and_queues_the_work(self, async_task):
        """The whole point of the background job: the request must not wait on
        per-row Supabase Auth calls."""
        async_task.return_value = "task-abc"
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.upload()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["task_id"], "task-abc")
        async_task.assert_called_once()
        self.assertEqual(
            async_task.call_args.args[0], "facilities.tasks.run_bulk_import"
        )
        self.assertEqual(
            async_task.call_args.kwargs["facility_id"], self.facility.id
        )
        # Nothing is created synchronously.
        self.assertEqual(Profile.objects.count(), 0)

    @mock.patch("facilities.views.async_task")
    def test_file_content_is_read_before_queueing(self, async_task):
        """The upload handle does not survive into the worker process, so the
        bytes must be captured in the request."""
        # A return_value is required: the view renders the task id into JSON,
        # and DRF's encoder recurses forever on a bare MagicMock.
        async_task.return_value = "task-xyz"
        self.authenticate(FACILITY_SUPABASE_ID)
        self.upload()
        self.assertEqual(async_task.call_args.kwargs["content"], CSV)

    def test_rejects_unsupported_file_type(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.upload(b"%PDF-1.4", "staff.pdf")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_empty_file(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.upload(b"", "staff.csv")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(BULK_IMPORT_MAX_BYTES=10)
    def test_rejects_oversized_file(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.upload()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_file_rejected(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class InviteLinkTests(SupabaseAuthMixin, APITestCase):
    url = "/api/facilities/invite-links/"

    def setUp(self):
        super().setUp()
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            supabase_user_id=FACILITY_SUPABASE_ID,
            status=Facility.Status.APPROVED,
        )

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.post(self.url, {}, format="json").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_creates_link_with_default_expiry(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invite = InviteLink.objects.get()
        self.assertEqual(invite.facility, self.facility)
        self.assertGreater(invite.expires_at, timezone.now())
        self.assertIn(str(invite.token), response.data["register_url"])

    def test_tokens_are_unguessable_and_unique(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        first = self.client.post(self.url, {}, format="json").data["token"]
        second = self.client.post(self.url, {}, format="json").data["token"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(str(first)), 36)

    def test_past_expiry_rejected(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(
            self.url,
            {"expires_at": (timezone.now() - timedelta(days=1)).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RegisterViaInviteTests(APITestCase):
    def setUp(self):
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            status=Facility.Status.APPROVED,
        )
        self.invite = InviteLink.objects.create(
            facility=self.facility, expires_at=timezone.now() + timedelta(days=7)
        )

    def url_for(self, token):
        return f"/api/profiles/register-via-invite/{token}/"

    def payload(self, **overrides):
        data = {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "07700900000",
            "license_number": "NMC-1",
            "license_body": "NMC",
        }
        data.update(overrides)
        return data

    def test_registers_into_the_phase0_verification_queue(self):
        """Definition-of-done item 3: lands as self_registered_unverified,
        exactly like the existing Phase 0 flow."""
        response = self.client.post(
            self.url_for(self.invite.token), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        profile = Profile.objects.get()
        self.assertEqual(
            profile.verification_state,
            Profile.VerificationState.SELF_REGISTERED_UNVERIFIED,
        )
        self.assertEqual(profile.onboarding_path, Profile.OnboardingPath.INVITE_LINK)
        self.assertEqual(profile.facility, self.facility)

    def test_needs_no_authentication(self):
        """The token is the authorisation — a professional may not have an
        account yet."""
        self.client.credentials()
        response = self.client.post(
            self.url_for(self.invite.token), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_expired_link_is_gone(self):
        self.invite.expires_at = timezone.now() - timedelta(seconds=1)
        self.invite.save()
        response = self.client.post(
            self.url_for(self.invite.token), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertEqual(Profile.objects.count(), 0)

    def test_unknown_token_is_404(self):
        response = self.client.post(
            self.url_for("11111111-2222-4333-8444-555555555555"),
            self.payload(),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_attach_to_a_different_facility(self):
        """facility is read-only here; supplying one must not override the
        facility the invite belongs to."""
        other = Facility.objects.create(
            name="Other", registration_number="REG-2", contact_email="o@example.com"
        )
        self.client.post(
            self.url_for(self.invite.token),
            self.payload(facility=other.id),
            format="json",
        )
        self.assertEqual(Profile.objects.get().facility, self.facility)

    def test_cannot_self_verify(self):
        self.client.post(
            self.url_for(self.invite.token),
            self.payload(verification_state="verified"),
            format="json",
        )
        self.assertEqual(
            Profile.objects.get().verification_state,
            Profile.VerificationState.SELF_REGISTERED_UNVERIFIED,
        )

    def test_duplicate_email_rejected(self):
        self.client.post(self.url_for(self.invite.token), self.payload(), format="json")
        response = self.client.post(
            self.url_for(self.invite.token), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class PushDeviceRegisterTests(SupabaseAuthMixin, APITestCase):
    url = "/api/devices/register/"

    def setUp(self):
        super().setUp()
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            status=Facility.Status.APPROVED,
        )
        self.professional = Profile.objects.create(
            full_name="Jane Doe",
            email="jane@example.com",
            license_number="NMC-1",
            license_body="NMC",
            facility=self.facility,
            supabase_user_id=PROFESSIONAL_SUPABASE_ID,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    def test_requires_authentication(self):
        response = self.client.post(
            self.url, {"fcm_token": "t", "device_type": "ios"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_account_without_a_profile_is_forbidden(self):
        self.authenticate("77777777-1111-4222-8333-444444444444")
        response = self.client.post(
            self.url, {"fcm_token": "t", "device_type": "ios"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_registers_a_device(self):
        self.authenticate(PROFESSIONAL_SUPABASE_ID)
        response = self.client.post(
            self.url, {"fcm_token": "tok-1", "device_type": "ios"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PushDevice.objects.get().profile, self.professional)

    def test_reregistering_the_same_token_is_not_an_error(self):
        """Mobile apps re-send the token on every launch; a 400 on the unique
        constraint would break push for that handset."""
        self.authenticate(PROFESSIONAL_SUPABASE_ID)
        self.client.post(
            self.url, {"fcm_token": "tok-1", "device_type": "ios"}, format="json"
        )
        response = self.client.post(
            self.url, {"fcm_token": "tok-1", "device_type": "ios"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PushDevice.objects.count(), 1)

    def test_handset_moving_to_another_professional_is_reassigned(self):
        """Otherwise the previous owner keeps receiving the new owner's shifts."""
        other = Profile.objects.create(
            full_name="Ade Bello",
            email="ade@example.com",
            license_number="NMC-2",
            license_body="NMC",
            facility=self.facility,
            supabase_user_id="88888888-1111-4222-8333-444444444444",
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )
        PushDevice.objects.create(
            profile=other, fcm_token="tok-shared", device_type="ios"
        )

        self.authenticate(PROFESSIONAL_SUPABASE_ID)
        response = self.client.post(
            self.url, {"fcm_token": "tok-shared", "device_type": "ios"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PushDevice.objects.count(), 1)
        self.assertEqual(PushDevice.objects.get().profile, self.professional)

    def test_invalid_device_type_rejected(self):
        self.authenticate(PROFESSIONAL_SUPABASE_ID)
        response = self.client.post(
            self.url, {"fcm_token": "tok-1", "device_type": "windows"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class FacilityStaffListTests(SupabaseAuthMixin, APITestCase):
    """Backs the rota's assignment dropdown."""

    url = "/api/facilities/staff/"

    def setUp(self):
        super().setUp()
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            supabase_user_id=FACILITY_SUPABASE_ID,
            status=Facility.Status.APPROVED,
        )
        Profile.objects.create(
            full_name="Jane Doe",
            email="jane@example.com",
            license_number="NMC-1",
            license_body="NMC",
            facility=self.facility,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_lists_own_staff(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([s["full_name"] for s in response.data], ["Jane Doe"])

    def test_never_lists_another_facilitys_staff(self):
        other = Facility.objects.create(
            name="Other",
            registration_number="REG-2",
            contact_email="other@example.com",
            supabase_user_id="dddddddd-1111-4222-8333-444444444444",
            status=Facility.Status.APPROVED,
        )
        Profile.objects.create(
            full_name="Outsider",
            email="outsider@example.com",
            license_number="NMC-9",
            license_body="NMC",
            facility=other,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )
        self.authenticate(FACILITY_SUPABASE_ID)
        names = [s["full_name"] for s in self.client.get(self.url).data]
        self.assertEqual(names, ["Jane Doe"])

    def test_does_not_leak_licence_or_phone(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        row = self.client.get(self.url).data[0]
        self.assertNotIn("license_number", row)
        self.assertNotIn("phone", row)


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class BulkImportStatusTests(SupabaseAuthMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            supabase_user_id=FACILITY_SUPABASE_ID,
            status=Facility.Status.APPROVED,
        )

    def url_for(self, task_id):
        return f"/api/facilities/bulk-import/{task_id}/"

    def make_task(self, facility_id, task_id="abc123", success=True):
        from django_q.models import Task

        return Task.objects.create(
            id=task_id,
            name=f"bulk-import-{facility_id}",
            func="facilities.tasks.run_bulk_import",
            started=timezone.now(),
            stopped=timezone.now(),
            success=success,
            result={"created": 2, "failed": 1, "errors": [{"row": 3, "error": "bad"}]},
        )

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.get(self.url_for("abc123")).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_unknown_task_reads_as_pending(self):
        """django-q2 writes no row until the job finishes, so 'not there yet'
        and 'never existed' are indistinguishable — and must not 404, or the
        UI would give up while the import is still running."""
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.get(self.url_for("not-a-task"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "pending")

    def test_returns_the_per_row_report(self):
        self.make_task(self.facility.id)
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.get(self.url_for("abc123"))
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["result"]["created"], 2)
        self.assertEqual(response.data["result"]["errors"][0]["row"], 3)

    def test_cannot_read_another_facilitys_import(self):
        other = Facility.objects.create(
            name="Other",
            registration_number="REG-2",
            contact_email="other@example.com",
            supabase_user_id="dddddddd-1111-4222-8333-444444444444",
            status=Facility.Status.APPROVED,
        )
        self.make_task(other.id, task_id="theirs")
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.get(self.url_for("theirs"))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_failed_task_reported_as_failed(self):
        self.make_task(self.facility.id, task_id="boom", success=False)
        self.authenticate(FACILITY_SUPABASE_ID)
        self.assertEqual(
            self.client.get(self.url_for("boom")).data["status"], "failed"
        )
