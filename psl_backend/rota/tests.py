"""Tests for shifts, publishing, push delivery and the reminder sweep."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility
from profiles.models import Profile, PushDevice
from rota.models import Shift
from rota.tasks import send_push_notification, send_shift_reminders

FACILITY_SUPABASE_ID = "cccccccc-1111-4222-8333-444444444444"
OTHER_FACILITY_SUPABASE_ID = "dddddddd-1111-4222-8333-444444444444"


def make_facility(supabase_id=FACILITY_SUPABASE_ID, email="oak@example.com", name="Oakwood"):
    return Facility.objects.create(
        name=name,
        registration_number=f"REG-{email}",
        contact_email=email,
        supabase_user_id=supabase_id,
        status=Facility.Status.APPROVED,
    )


def make_profile(facility, email="jane@example.com", name="Jane Doe", supabase_id=None):
    return Profile.objects.create(
        full_name=name,
        email=email,
        license_number="NMC-1",
        license_body="NMC",
        facility=facility,
        supabase_user_id=supabase_id,
        onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
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
class ShiftEndpointTests(SupabaseAuthMixin, APITestCase):
    url = "/api/rota/shifts/"

    def setUp(self):
        super().setUp()
        self.facility = make_facility()
        self.professional = make_profile(self.facility)
        self.start = timezone.now() + timedelta(days=1)

    def payload(self, **overrides):
        data = {
            "role": "Night nurse",
            "start_time": self.start.isoformat(),
            "end_time": (self.start + timedelta(hours=8)).isoformat(),
            "professional": self.professional.id,
        }
        data.update(overrides)
        return data

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.post(self.url, self.payload(), format="json").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_account_without_a_facility_is_forbidden(self):
        self.authenticate("99999999-1111-4222-8333-444444444444")
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_creates_unpublished_draft_shift(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        shift = Shift.objects.get()
        self.assertFalse(shift.is_published)
        self.assertIsNone(shift.published_at)
        self.assertEqual(shift.facility, self.facility)

    def test_cannot_publish_via_create_payload(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        self.client.post(self.url, self.payload(is_published=True), format="json")
        self.assertFalse(Shift.objects.get().is_published)

    def test_shift_may_be_unassigned(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(
            self.url, self.payload(professional=None), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(Shift.objects.get().professional)

    def test_end_before_start_rejected(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(
            self.url,
            self.payload(end_time=(self.start - timedelta(hours=1)).isoformat()),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_roster_another_facilitys_professional(self):
        other = make_facility(
            supabase_id=OTHER_FACILITY_SUPABASE_ID, email="other@example.com", name="Other"
        )
        outsider = make_profile(other, email="outsider@example.com", name="Outsider")
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(
            self.url, self.payload(professional=outsider.id), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Shift.objects.count(), 0)

    def test_list_only_shows_own_facility(self):
        other = make_facility(
            supabase_id=OTHER_FACILITY_SUPABASE_ID, email="other@example.com", name="Other"
        )
        Shift.objects.create(
            facility=other,
            role="Theirs",
            start_time=self.start,
            end_time=self.start + timedelta(hours=4),
        )
        Shift.objects.create(
            facility=self.facility,
            role="Mine",
            start_time=self.start,
            end_time=self.start + timedelta(hours=4),
        )
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.get(self.url)
        self.assertEqual([s["role"] for s in response.data], ["Mine"])

    @override_settings(REQUIRE_APPROVED_FACILITY=True)
    def test_unapproved_facility_is_blocked(self):
        self.facility.status = Facility.Status.PENDING
        self.facility.save()
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class PublishShiftsTests(SupabaseAuthMixin, APITestCase):
    url = "/api/rota/shifts/publish/"

    def setUp(self):
        super().setUp()
        self.facility = make_facility()
        self.professional = make_profile(self.facility)
        start = timezone.now() + timedelta(days=1)
        self.assigned = Shift.objects.create(
            facility=self.facility,
            professional=self.professional,
            role="Night nurse",
            start_time=start,
            end_time=start + timedelta(hours=8),
        )
        self.unassigned = Shift.objects.create(
            facility=self.facility,
            role="Day nurse",
            start_time=start,
            end_time=start + timedelta(hours=8),
        )

    @mock.patch("rota.views.async_task")
    def test_publishes_and_queues_one_push_per_assigned_shift(self, async_task):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(
            self.url,
            {"shift_ids": [self.assigned.id, self.unassigned.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["published"], 2)
        self.assertEqual(response.data["notifications_queued"], 1)

        self.assigned.refresh_from_db()
        self.unassigned.refresh_from_db()
        self.assertTrue(self.assigned.is_published)
        self.assertIsNotNone(self.assigned.published_at)
        self.assertTrue(self.unassigned.is_published)

        # Queued, never sent inline.
        self.assertEqual(async_task.call_count, 1)
        self.assertEqual(
            async_task.call_args.kwargs["professional_id"], self.professional.id
        )

    @mock.patch("rota.views.async_task")
    def test_publish_is_recorded_in_history(self, _async_task):
        """The spec sketch used queryset.update(), which writes no history."""
        self.authenticate(FACILITY_SUPABASE_ID)
        self.client.post(self.url, {"shift_ids": [self.assigned.id]}, format="json")
        self.assigned.refresh_from_db()
        self.assertEqual(self.assigned.history.count(), 2)
        flags = [h.is_published for h in self.assigned.history.order_by("history_date")]
        self.assertEqual(flags, [False, True])

    @mock.patch("rota.views.async_task")
    def test_cannot_publish_another_facilitys_shifts(self, async_task):
        other = make_facility(
            supabase_id=OTHER_FACILITY_SUPABASE_ID, email="other@example.com", name="Other"
        )
        start = timezone.now() + timedelta(days=1)
        theirs = Shift.objects.create(
            facility=other,
            role="Theirs",
            start_time=start,
            end_time=start + timedelta(hours=4),
        )
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(self.url, {"shift_ids": [theirs.id]}, format="json")
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_published)
        self.assertEqual(response.data["published"], 0)
        self.assertEqual(response.data["not_published"], [theirs.id])
        async_task.assert_not_called()

    @mock.patch("rota.views.async_task")
    def test_republishing_is_a_no_op(self, async_task):
        self.authenticate(FACILITY_SUPABASE_ID)
        self.client.post(self.url, {"shift_ids": [self.assigned.id]}, format="json")
        async_task.reset_mock()
        response = self.client.post(
            self.url, {"shift_ids": [self.assigned.id]}, format="json"
        )
        self.assertEqual(response.data["published"], 0)
        async_task.assert_not_called()

    def test_empty_shift_ids_rejected(self):
        self.authenticate(FACILITY_SUPABASE_ID)
        response = self.client.post(self.url, {"shift_ids": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SendPushNotificationTests(TestCase):
    def setUp(self):
        self.facility = make_facility()
        self.professional = make_profile(self.facility)

    def test_no_devices_is_not_an_error(self):
        result = send_push_notification(self.professional.id, "t", "b")
        self.assertEqual(result["reason"], "no-devices")

    def test_missing_profile_is_not_an_error(self):
        result = send_push_notification(999999, "t", "b")
        self.assertEqual(result["reason"], "profile-missing")

    @mock.patch("rota.tasks.send_to_tokens")
    def test_sends_to_every_registered_device(self, send):
        send.return_value = (2, 0, [])
        PushDevice.objects.create(
            profile=self.professional, fcm_token="tok-a", device_type="ios"
        )
        PushDevice.objects.create(
            profile=self.professional, fcm_token="tok-b", device_type="android"
        )
        result = send_push_notification(self.professional.id, "Title", "Body")
        self.assertEqual(result["sent"], 2)
        self.assertCountEqual(send.call_args.args[0], ["tok-a", "tok-b"])

    @mock.patch("rota.tasks.send_to_tokens")
    def test_unregistered_tokens_are_pruned(self, send):
        """A token FCM calls unregistered never recovers, so it must be
        deleted rather than retried on every future publish."""
        send.return_value = (0, 1, ["tok-dead"])
        PushDevice.objects.create(
            profile=self.professional, fcm_token="tok-dead", device_type="ios"
        )
        result = send_push_notification(self.professional.id, "t", "b")
        self.assertEqual(result["pruned_devices"], 1)
        self.assertEqual(PushDevice.objects.count(), 0)


@override_settings(
    SHIFT_REMINDER_LEAD_MINUTES=60,
    SHIFT_REMINDER_WINDOW_MINUTES=20,
)
class ShiftReminderSweepTests(TestCase):
    def setUp(self):
        self.facility = make_facility()
        self.professional = make_profile(self.facility)

    def make_shift(self, minutes_from_now, published=True, professional=True, **kwargs):
        start = timezone.now() + timedelta(minutes=minutes_from_now)
        return Shift.objects.create(
            facility=self.facility,
            professional=self.professional if professional else None,
            role="Night nurse",
            start_time=start,
            end_time=start + timedelta(hours=8),
            is_published=published,
            **kwargs,
        )

    @mock.patch("rota.tasks.async_task")
    def test_reminds_a_shift_inside_the_window(self, async_task):
        shift = self.make_shift(60)
        result = send_shift_reminders()
        self.assertEqual(result["reminded"], 1)
        shift.refresh_from_db()
        self.assertTrue(shift.reminder_sent)
        self.assertIsNotNone(shift.reminder_sent_at)
        async_task.assert_called_once()

    @mock.patch("rota.tasks.async_task")
    def test_reminder_fires_exactly_once_across_repeated_sweeps(self, async_task):
        """The idempotency requirement: overlapping sweeps must not double-send."""
        self.make_shift(60)
        first = send_shift_reminders()
        second = send_shift_reminders()
        third = send_shift_reminders()
        self.assertEqual(first["reminded"], 1)
        self.assertEqual(second["reminded"], 0)
        self.assertEqual(third["reminded"], 0)
        self.assertEqual(async_task.call_count, 1)

    @mock.patch("rota.tasks.async_task")
    def test_ignores_shifts_outside_the_window(self, async_task):
        self.make_shift(5)      # too soon
        self.make_shift(240)    # too far away
        result = send_shift_reminders()
        self.assertEqual(result["reminded"], 0)
        async_task.assert_not_called()

    @mock.patch("rota.tasks.async_task")
    def test_ignores_unpublished_shifts(self, async_task):
        self.make_shift(60, published=False)
        self.assertEqual(send_shift_reminders()["reminded"], 0)
        async_task.assert_not_called()

    @mock.patch("rota.tasks.async_task")
    def test_ignores_unassigned_shifts(self, async_task):
        self.make_shift(60, professional=False)
        self.assertEqual(send_shift_reminders()["reminded"], 0)
        async_task.assert_not_called()

    @mock.patch("rota.tasks.async_task")
    def test_past_shifts_are_never_reminded(self, async_task):
        self.make_shift(-60)
        self.assertEqual(send_shift_reminders()["reminded"], 0)
        async_task.assert_not_called()
