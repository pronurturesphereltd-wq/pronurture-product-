"""Leave applications: submission, the facility approval queue, and isolation."""

from datetime import date, timedelta
from unittest import mock

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility
from leave.models import LeaveApplication
from profiles.models import Profile

OAK_SUB = "11111111-0000-4000-8000-000000000001"
IVY_SUB = "22222222-0000-4000-8000-000000000002"
ALICE_SUB = "aaaaaaaa-0000-4000-8000-000000000001"
BOB_SUB = "bbbbbbbb-0000-4000-8000-000000000002"
DANA_SUB = "dddddddd-0000-4000-8000-000000000004"
UNATTACHED_SUB = "eeeeeeee-0000-4000-8000-000000000005"

LIST_URL = "/api/leave/applications/"


def make_facility(name, email, sub, status_=Facility.Status.APPROVED):
    return Facility.objects.create(
        name=name,
        registration_number=f"REG-{name}",
        contact_email=email,
        supabase_user_id=sub,
        status=status_,
    )


def make_profile(facility, name, email, sub):
    return Profile.objects.create(
        full_name=name,
        email=email,
        license_number=f"NMC-{name}",
        license_body="NMC",
        facility=facility,
        supabase_user_id=sub,
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

        # Queued rather than sent: assert on what was queued, and keep the
        # suite from reaching for Firebase.
        push = mock.patch("leave.views.async_task", return_value="task-id")
        self.async_task = push.start()
        self.addCleanup(push.stop)

    def authenticate(self, sub):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(sub=sub)}")


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class LeaveBase(SupabaseAuthMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.oakwood = make_facility("Oakwood", "oak@example.com", OAK_SUB)
        self.ivy = make_facility("Ivy", "ivy@example.com", IVY_SUB)
        self.alice = make_profile(self.oakwood, "Alice", "alice@example.com", ALICE_SUB)
        self.bob = make_profile(self.oakwood, "Bob", "bob@example.com", BOB_SUB)
        self.dana = make_profile(self.ivy, "Dana", "dana@example.com", DANA_SUB)

        self.start = date.today() + timedelta(days=10)
        self.end = self.start + timedelta(days=4)

    def apply(self, start=None, end=None, reason="Family visit"):
        return self.client.post(
            LIST_URL,
            {
                "start_date": str(start or self.start),
                "end_date": str(end or self.end),
                "reason": reason,
            },
            format="json",
        )

    def submitted(self, professional=None, start=None, end=None):
        return LeaveApplication.objects.create(
            professional=professional or self.alice,
            start_date=start or self.start,
            end_date=end or self.end,
            reason="Annual leave",
        )


class LeaveSubmissionTests(LeaveBase):
    def test_requires_authentication(self):
        self.assertEqual(self.apply().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_professional_can_submit(self):
        self.authenticate(ALICE_SUB)
        response = self.apply()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        application = LeaveApplication.objects.get()
        self.assertEqual(application.professional, self.alice)
        self.assertEqual(application.status, LeaveApplication.Status.SUBMITTED)
        self.assertIsNone(application.decided_at)
        # Inclusive: the 10th to the 14th is five days, not four.
        self.assertEqual(response.data["days"], 5)

    def test_applicant_comes_from_the_token_not_the_body(self):
        """Otherwise anyone could file leave in a colleague's name."""
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            LIST_URL,
            {
                "professional": self.bob.id,
                "start_date": str(self.start),
                "end_date": str(self.end),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(LeaveApplication.objects.get().professional, self.alice)

    def test_status_cannot_be_set_on_submission(self):
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            LIST_URL,
            {
                "start_date": str(self.start),
                "end_date": str(self.end),
                "status": "approved",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            LeaveApplication.objects.get().status, LeaveApplication.Status.SUBMITTED
        )

    def test_end_date_before_start_date_is_rejected(self):
        self.authenticate(ALICE_SUB)
        response = self.apply(start=self.end, end=self.start)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", response.data)

    def test_single_day_leave_is_allowed(self):
        self.authenticate(ALICE_SUB)
        response = self.apply(start=self.start, end=self.start)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["days"], 1)

    def test_overlapping_application_is_rejected(self):
        self.submitted()
        self.authenticate(ALICE_SUB)
        response = self.apply(
            start=self.start + timedelta(days=2), end=self.end + timedelta(days=2)
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(LeaveApplication.objects.count(), 1)

    def test_non_overlapping_application_is_allowed(self):
        self.submitted()
        self.authenticate(ALICE_SUB)
        response = self.apply(
            start=self.end + timedelta(days=1), end=self.end + timedelta(days=3)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(LeaveApplication.objects.count(), 2)

    def test_can_reapply_over_declined_dates(self):
        """A refusal is not a permanent block on those dates."""
        declined = self.submitted()
        declined.status = LeaveApplication.Status.DECLINED
        declined.save()

        self.authenticate(ALICE_SUB)
        self.assertEqual(self.apply().status_code, status.HTTP_201_CREATED)

    def test_colleague_overlap_does_not_block(self):
        """The guard is per professional, not per facility."""
        self.submitted(professional=self.bob)
        self.authenticate(ALICE_SUB)
        self.assertEqual(self.apply().status_code, status.HTTP_201_CREATED)

    def test_facility_cannot_submit_leave(self):
        self.authenticate(OAK_SUB)
        self.assertEqual(self.apply().status_code, status.HTTP_403_FORBIDDEN)

    def test_profile_with_no_facility_cannot_submit(self):
        """There would be no queue for it to land in."""
        Profile.objects.create(
            full_name="Nomad",
            email="nomad@example.com",
            license_number="NMC-N",
            license_body="NMC",
            supabase_user_id=UNATTACHED_SUB,
        )
        self.authenticate(UNATTACHED_SUB)
        response = self.apply()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not attached to a facility", response.data["detail"])

    def test_unknown_account_is_denied(self):
        self.authenticate("99999999-0000-4000-8000-000000000099")
        self.assertEqual(self.apply().status_code, status.HTTP_403_FORBIDDEN)


class LeaveQueueTests(LeaveBase):
    def test_facility_sees_its_whole_roster(self):
        self.submitted(professional=self.alice)
        self.submitted(professional=self.bob)
        self.authenticate(OAK_SUB)

        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            {row["professional_name"] for row in response.data}, {"Alice", "Bob"}
        )

    def test_professional_sees_only_their_own(self):
        self.submitted(professional=self.alice)
        self.submitted(professional=self.bob)
        self.authenticate(ALICE_SUB)

        response = self.client.get(LIST_URL)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["professional_name"], "Alice")

    def test_facility_cannot_see_another_facilitys_queue(self):
        self.submitted(professional=self.dana)  # Ivy's staff
        self.authenticate(OAK_SUB)
        self.assertEqual(self.client.get(LIST_URL).data, [])

    def test_professional_cannot_see_another_facilitys_applications(self):
        """`professional=self` is narrower than facility scoping, so this
        cannot leak — asserted anyway, because the day someone widens that
        filter to the roster this is the test that catches it."""
        self.submitted(professional=self.dana)  # Ivy's staff
        self.authenticate(ALICE_SUB)
        self.assertEqual(self.client.get(LIST_URL).data, [])

    def test_status_filter_does_not_cross_the_facility_boundary(self):
        """Filter ordering bug bait: the facility scope has to survive a
        `?status=` narrowing rather than being replaced by it."""
        self.submitted(professional=self.dana)  # Ivy's staff, submitted
        self.authenticate(OAK_SUB)
        self.assertEqual(self.client.get(f"{LIST_URL}?status=submitted").data, [])

    def test_status_filter(self):
        self.submitted(professional=self.alice)
        approved = self.submitted(
            professional=self.bob,
            start=self.start + timedelta(days=30),
            end=self.end + timedelta(days=30),
        )
        approved.status = LeaveApplication.Status.APPROVED
        approved.save()

        self.authenticate(OAK_SUB)
        response = self.client.get(f"{LIST_URL}?status=submitted")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["professional_name"], "Alice")

    def test_professional_reads_the_decision_without_a_push(self):
        """Spec: the state change must be visible via a plain GET, not only
        through a notification that may never be delivered."""
        application = self.submitted()
        self.authenticate(OAK_SUB)
        self.client.post(f"{LIST_URL}{application.id}/approve/")

        self.authenticate(ALICE_SUB)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.data[0]["status"], "approved")
        self.assertIsNotNone(response.data[0]["decided_at"])


class LeaveDecisionTests(LeaveBase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()

    def approve_url(self, pk=None):
        return f"{LIST_URL}{pk or self.application.id}/approve/"

    def decline_url(self, pk=None):
        return f"{LIST_URL}{pk or self.application.id}/decline/"

    def test_facility_can_approve(self):
        self.authenticate(OAK_SUB)
        response = self.client.post(self.approve_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, LeaveApplication.Status.APPROVED)
        self.assertIsNotNone(self.application.decided_at)

    def test_facility_can_decline(self):
        self.authenticate(OAK_SUB)
        self.client.post(self.decline_url())
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, LeaveApplication.Status.DECLINED)

    def test_decision_is_recorded_in_history(self):
        self.authenticate(OAK_SUB)
        self.client.post(self.approve_url())
        states = list(
            self.application.history.order_by("history_date").values_list(
                "status", flat=True
            )
        )
        self.assertEqual(states, ["submitted", "approved"])

    def test_decision_queues_a_notification(self):
        self.authenticate(OAK_SUB)
        self.client.post(self.approve_url())

        self.async_task.assert_called_once()
        _args, kwargs = self.async_task.call_args
        self.assertEqual(kwargs["professional_id"], self.alice.id)
        self.assertEqual(kwargs["title"], "Leave approved")

    def test_deciding_twice_conflicts(self):
        self.authenticate(OAK_SUB)
        self.assertEqual(
            self.client.post(self.approve_url()).status_code, status.HTTP_200_OK
        )
        second = self.client.post(self.decline_url())
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)

        self.application.refresh_from_db()
        # The first decision stands rather than being overwritten.
        self.assertEqual(self.application.status, LeaveApplication.Status.APPROVED)
        self.assertEqual(self.async_task.call_count, 1)

    def test_professional_cannot_approve_their_own_leave(self):
        self.authenticate(ALICE_SUB)
        self.assertEqual(
            self.client.post(self.approve_url()).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_another_facility_cannot_approve(self):
        self.authenticate(IVY_SUB)
        response = self.client.post(self.approve_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, LeaveApplication.Status.SUBMITTED)

    def test_unapproved_facility_cannot_decide(self):
        self.oakwood.status = Facility.Status.SUSPENDED
        self.oakwood.save()
        self.authenticate(OAK_SUB)
        self.assertEqual(
            self.client.post(self.approve_url()).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_unknown_application_is_not_found(self):
        self.authenticate(OAK_SUB)
        self.assertEqual(
            self.client.post(self.approve_url(pk=999999)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    # Decline is a separate route and a separate as_view(), so the isolation
    # checks are repeated against it rather than assumed from the shared base.

    def test_another_facility_cannot_decline(self):
        self.authenticate(IVY_SUB)
        response = self.client.post(self.decline_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, LeaveApplication.Status.SUBMITTED)

    def test_professional_cannot_decline(self):
        self.authenticate(ALICE_SUB)
        self.assertEqual(
            self.client.post(self.decline_url()).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_another_facilitys_application_looks_like_a_missing_one(self):
        """Same reasoning as the swap endpoint: if a foreign id answered
        differently from an absent one, the difference would enumerate every
        leave application on the platform."""
        self.authenticate(IVY_SUB)
        foreign = self.client.post(self.approve_url())
        missing = self.client.post(self.approve_url(pk=999999))
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.data, missing.data)

    def test_colleague_cannot_decide_another_professionals_leave(self):
        """Not just a foreign facility — a peer on the same roster has no
        decision rights either."""
        self.authenticate(BOB_SUB)
        self.assertEqual(
            self.client.post(self.approve_url()).status_code, status.HTTP_403_FORBIDDEN
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, LeaveApplication.Status.SUBMITTED)
