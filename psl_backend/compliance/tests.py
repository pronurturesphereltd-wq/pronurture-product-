"""The compliance sweep, its idempotency, and the facility-facing endpoints."""

from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from django_q.models import Schedule
from rest_framework import status
from rest_framework.test import APITestCase

from compliance.models import ComplianceAlert
from compliance.tasks import check_compliance
from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility
from profiles.models import Profile

OAK_SUB = "11111111-0000-4000-8000-000000000001"
IVY_SUB = "22222222-0000-4000-8000-000000000002"
ALICE_SUB = "aaaaaaaa-0000-4000-8000-000000000001"

LIST_URL = "/api/facilities/compliance-alerts/"


def make_facility(name, email, sub):
    return Facility.objects.create(
        name=name,
        registration_number=f"REG-{name}",
        contact_email=email,
        supabase_user_id=sub,
        status=Facility.Status.APPROVED,
    )


def make_profile(facility, name, email, expiry=None, sub=None):
    return Profile.objects.create(
        full_name=name,
        email=email,
        license_number=f"NMC-{name}",
        license_body="NMC",
        facility=facility,
        supabase_user_id=sub,
        license_expiry_date=expiry,
        onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
    )


@override_settings(COMPLIANCE_LICENSE_LEAD_DAYS=30)
class ComplianceSweepTests(TestCase):
    def setUp(self):
        self.facility = make_facility("Oakwood", "oak@example.com", OAK_SUB)
        self.today = date.today()

    def test_licence_expiring_inside_the_window_raises_one_alert(self):
        profile = make_profile(
            self.facility, "Alice", "alice@example.com", self.today + timedelta(days=10)
        )
        result = check_compliance()

        self.assertEqual(result["alerts_created"], 1)
        alert = ComplianceAlert.objects.get()
        self.assertEqual(alert.profile, profile)
        self.assertEqual(alert.alert_type, ComplianceAlert.AlertType.LICENSE_EXPIRING)
        self.assertEqual(alert.due_date, profile.license_expiry_date)
        self.assertEqual(alert.status, ComplianceAlert.Status.OPEN)

    def test_second_run_creates_nothing(self):
        """The whole point of the guard: a daily sweep must not accumulate a
        new alert every day for the same licence."""
        make_profile(
            self.facility, "Alice", "alice@example.com", self.today + timedelta(days=10)
        )
        check_compliance()
        result = check_compliance()

        self.assertEqual(result["alerts_created"], 0)
        self.assertEqual(ComplianceAlert.objects.count(), 1)

    def test_licence_beyond_the_window_is_left_alone(self):
        make_profile(
            self.facility, "Far", "far@example.com", self.today + timedelta(days=90)
        )
        self.assertEqual(check_compliance()["alerts_created"], 0)
        self.assertEqual(ComplianceAlert.objects.count(), 0)

    def test_boundary_day_is_included(self):
        make_profile(
            self.facility, "Edge", "edge@example.com", self.today + timedelta(days=30)
        )
        self.assertEqual(check_compliance()["alerts_created"], 1)

    def test_already_expired_licence_is_alerted(self):
        """An expired licence is more of a problem than one expiring soon, not
        less — `lte` has to include the past."""
        make_profile(
            self.facility, "Lapsed", "lapsed@example.com", self.today - timedelta(days=5)
        )
        self.assertEqual(check_compliance()["alerts_created"], 1)
        self.assertEqual(ComplianceAlert.objects.get().due_date, self.today - timedelta(days=5))

    def test_profile_without_an_expiry_date_is_ignored(self):
        make_profile(self.facility, "Unknown", "unknown@example.com", None)
        self.assertEqual(check_compliance()["alerts_created"], 0)

    def test_resolved_alert_is_raised_again_while_the_licence_still_expires(self):
        """Documented behaviour, not a loop bug: resolving does not fix the
        underlying licence. It stops recurring when the expiry is renewed."""
        make_profile(
            self.facility, "Alice", "alice@example.com", self.today + timedelta(days=10)
        )
        check_compliance()
        ComplianceAlert.objects.update(
            status=ComplianceAlert.Status.RESOLVED, resolved_at=timezone.now()
        )

        self.assertEqual(check_compliance()["alerts_created"], 1)
        self.assertEqual(ComplianceAlert.objects.count(), 2)
        self.assertEqual(
            ComplianceAlert.objects.filter(status=ComplianceAlert.Status.OPEN).count(), 1
        )

    def test_renewing_the_licence_stops_the_alerts(self):
        profile = make_profile(
            self.facility, "Alice", "alice@example.com", self.today + timedelta(days=10)
        )
        check_compliance()
        ComplianceAlert.objects.update(
            status=ComplianceAlert.Status.RESOLVED, resolved_at=timezone.now()
        )
        profile.license_expiry_date = self.today + timedelta(days=400)
        profile.save()

        self.assertEqual(check_compliance()["alerts_created"], 0)

    def test_sweep_covers_every_facility(self):
        """It is a PSL-wide job, not scoped to one tenant — only the reading of
        the results is."""
        ivy = make_facility("Ivy", "ivy@example.com", IVY_SUB)
        make_profile(
            self.facility, "Alice", "alice@example.com", self.today + timedelta(days=5)
        )
        make_profile(ivy, "Dana", "dana@example.com", self.today + timedelta(days=5))

        self.assertEqual(check_compliance()["alerts_created"], 2)

    def test_database_refuses_a_second_open_alert(self):
        """The enforced half of the guard. If the .exclude() were ever wrong,
        this constraint is what still stops duplicates."""
        profile = make_profile(
            self.facility, "Alice", "alice@example.com", self.today + timedelta(days=10)
        )
        ComplianceAlert.objects.create(
            profile=profile,
            alert_type=ComplianceAlert.AlertType.LICENSE_EXPIRING,
            due_date=profile.license_expiry_date,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ComplianceAlert.objects.create(
                    profile=profile,
                    alert_type=ComplianceAlert.AlertType.LICENSE_EXPIRING,
                    due_date=profile.license_expiry_date,
                )

    def test_constraint_violation_is_counted_not_fatal(self):
        """A run racing another must finish its remaining profiles rather than
        dying on the first collision."""
        make_profile(
            self.facility, "Alice", "alice@example.com", self.today + timedelta(days=5)
        )
        make_profile(
            self.facility, "Bob", "bob@example.com", self.today + timedelta(days=6)
        )

        real_create = ComplianceAlert.objects.create
        calls = {"n": 0}

        def flaky_create(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError("simulated concurrent insert")
            return real_create(**kwargs)

        with mock.patch.object(
            ComplianceAlert.objects, "create", side_effect=flaky_create
        ):
            result = check_compliance()

        self.assertEqual(result["alerts_created"], 1)
        self.assertEqual(result["already_open"], 1)

    @override_settings(COMPLIANCE_LICENSE_LEAD_DAYS=90)
    def test_lead_time_is_configurable(self):
        make_profile(
            self.facility, "Far", "far@example.com", self.today + timedelta(days=60)
        )
        result = check_compliance()
        self.assertEqual(result["lead_days"], 90)
        self.assertEqual(result["alerts_created"], 1)


class ComplianceScheduleTests(TestCase):
    def test_command_registers_a_daily_schedule_with_an_explicit_next_run(self):
        call_command("setup_compliance_checks", stdout=StringIO())

        schedule = Schedule.objects.get(name="compliance-checks")
        self.assertEqual(schedule.func, "compliance.tasks.check_compliance")
        self.assertEqual(schedule.schedule_type, Schedule.DAILY)
        self.assertEqual(schedule.repeats, -1)
        # Written explicitly rather than defaulted: a Schedule holds an
        # absolute timestamp and does not self-heal after a clock correction.
        self.assertIsNotNone(schedule.next_run)
        self.assertLessEqual(schedule.next_run, timezone.now())

    def test_command_is_idempotent(self):
        call_command("setup_compliance_checks", stdout=StringIO())
        call_command("setup_compliance_checks", stdout=StringIO())
        self.assertEqual(Schedule.objects.filter(name="compliance-checks").count(), 1)

    def test_run_now_executes_the_sweep_inline(self):
        facility = make_facility("Oakwood", "oak@example.com", OAK_SUB)
        make_profile(
            facility, "Alice", "alice@example.com", date.today() + timedelta(days=10)
        )
        out = StringIO()
        call_command("setup_compliance_checks", "--run-now", stdout=out)

        self.assertEqual(ComplianceAlert.objects.count(), 1)
        self.assertIn("Ran once inline", out.getvalue())


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
    COMPLIANCE_LICENSE_LEAD_DAYS=30,
)
class ComplianceEndpointTests(APITestCase):
    def setUp(self):
        patcher = mock.patch(
            "core.authentication.get_jwk_client",
            return_value=mock.Mock(
                get_signing_key_from_jwt=mock.Mock(return_value=StubSigningKey())
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.today = date.today()
        self.oakwood = make_facility("Oakwood", "oak@example.com", OAK_SUB)
        self.ivy = make_facility("Ivy", "ivy@example.com", IVY_SUB)
        self.alice = make_profile(
            self.oakwood,
            "Alice",
            "alice@example.com",
            self.today + timedelta(days=10),
            sub=ALICE_SUB,
        )
        self.dana = make_profile(
            self.ivy, "Dana", "dana@example.com", self.today + timedelta(days=10)
        )
        check_compliance()
        self.alert = ComplianceAlert.objects.get(profile=self.alice)
        self.ivy_alert = ComplianceAlert.objects.get(profile=self.dana)

    def authenticate(self, sub):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(sub=sub)}")

    def resolve_url(self, pk=None):
        return f"{LIST_URL}{pk or self.alert.id}/resolve/"

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.get(LIST_URL).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_facility_sees_only_its_own_alerts(self):
        self.authenticate(OAK_SUB)
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["professional_name"], "Alice")
        self.assertEqual(response.data[0]["days_until_due"], 10)

    def test_professional_cannot_read_the_facility_dashboard(self):
        self.authenticate(ALICE_SUB)
        self.assertEqual(
            self.client.get(LIST_URL).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_resolved_alerts_are_hidden_by_default(self):
        self.authenticate(OAK_SUB)
        self.client.post(self.resolve_url())
        self.assertEqual(self.client.get(LIST_URL).data, [])

    def test_status_all_shows_history(self):
        self.authenticate(OAK_SUB)
        self.client.post(self.resolve_url())
        response = self.client.get(f"{LIST_URL}?status=all")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], "resolved")

    def test_facility_can_resolve(self):
        self.authenticate(OAK_SUB)
        response = self.client.post(self.resolve_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, ComplianceAlert.Status.RESOLVED)
        self.assertIsNotNone(self.alert.resolved_at)

    def test_resolving_twice_conflicts(self):
        self.authenticate(OAK_SUB)
        self.client.post(self.resolve_url())
        self.assertEqual(
            self.client.post(self.resolve_url()).status_code, status.HTTP_409_CONFLICT
        )

    def test_facility_cannot_resolve_another_facilitys_alert(self):
        self.authenticate(OAK_SUB)
        response = self.client.post(self.resolve_url(pk=self.ivy_alert.id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.ivy_alert.refresh_from_db()
        self.assertEqual(self.ivy_alert.status, ComplianceAlert.Status.OPEN)

    def test_expired_licence_reads_as_negative_days(self):
        self.alice.license_expiry_date = self.today - timedelta(days=3)
        self.alice.save()
        self.alert.due_date = self.alice.license_expiry_date
        self.alert.save()

        self.authenticate(OAK_SUB)
        self.assertEqual(self.client.get(LIST_URL).data[0]["days_until_due"], -3)

    def test_professional_cannot_resolve_an_alert(self):
        """The list is professional-proof; assert the write path is too."""
        self.authenticate(ALICE_SUB)
        response = self.client.post(self.resolve_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, ComplianceAlert.Status.OPEN)

    def test_another_facilitys_alert_looks_like_a_missing_one(self):
        """A foreign id and an absent id have to answer identically, or the
        difference enumerates every alert on the platform."""
        self.authenticate(OAK_SUB)
        foreign = self.client.post(self.resolve_url(pk=self.ivy_alert.id))
        missing = self.client.post(self.resolve_url(pk=999999))
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.data, missing.data)

    def test_status_all_does_not_cross_the_facility_boundary(self):
        """Filter ordering bug bait: `?status=all` skips the status filter, so
        the facility scope has to be applied independently of it."""
        self.authenticate(OAK_SUB)
        rows = self.client.get(f"{LIST_URL}?status=all").data
        self.assertEqual([row["professional_name"] for row in rows], ["Alice"])

    def test_unapproved_facility_is_denied(self):
        self.oakwood.status = Facility.Status.SUSPENDED
        self.oakwood.save()
        self.authenticate(OAK_SUB)
        self.assertEqual(
            self.client.get(LIST_URL).status_code, status.HTTP_403_FORBIDDEN
        )
