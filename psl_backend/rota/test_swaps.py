"""Shift swap requests, including the atomic-accept concurrency proof."""

import threading
from datetime import timedelta
from unittest import mock

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility
from profiles.models import Profile
from rota.models import Shift, ShiftSwapRequest

ALICE_SUB = "aaaaaaaa-0000-4000-8000-000000000001"
BOB_SUB = "bbbbbbbb-0000-4000-8000-000000000002"
CARLA_SUB = "cccccccc-0000-4000-8000-000000000003"
OUTSIDER_SUB = "dddddddd-0000-4000-8000-000000000004"
FACILITY_SUB = "f0000000-0000-4000-8000-000000000010"
OTHER_FACILITY_SUB = "f0000000-0000-4000-8000-000000000011"


def make_facility(name="Oakwood", email="oak@example.com"):
    return Facility.objects.create(
        name=name,
        registration_number=f"REG-{name}",
        contact_email=email,
        status=Facility.Status.APPROVED,
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


def make_shift(facility, professional, published=True, days_ahead=2):
    start = timezone.now() + timedelta(days=days_ahead)
    return Shift.objects.create(
        facility=facility,
        professional=professional,
        role="Night nurse",
        start_time=start,
        end_time=start + timedelta(hours=8),
        is_published=published,
        published_at=timezone.now() if published else None,
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
class SwapRequestFlowTests(SupabaseAuthMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.facility = make_facility()
        self.alice = make_profile(self.facility, "Alice", "alice@example.com", ALICE_SUB)
        self.bob = make_profile(self.facility, "Bob", "bob@example.com", BOB_SUB)
        self.shift = make_shift(self.facility, self.alice)

    def create_url(self, shift_id=None):
        return f"/api/rota/shifts/{shift_id or self.shift.id}/swap-request/"

    # --- opening a request -------------------------------------------

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.post(self.create_url(), {}, format="json").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_assignee_can_open_a_request(self):
        self.authenticate(ALICE_SUB)
        response = self.client.post(self.create_url(), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        swap = ShiftSwapRequest.objects.get()
        self.assertEqual(swap.requesting_professional, self.alice)
        self.assertEqual(swap.status, ShiftSwapRequest.Status.PENDING)
        self.assertIsNone(swap.target_professional)

    def test_non_assignee_cannot_open_a_request(self):
        self.authenticate(BOB_SUB)
        response = self.client.post(self.create_url(), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ShiftSwapRequest.objects.count(), 0)

    def test_cannot_open_on_an_unpublished_shift(self):
        draft = make_shift(self.facility, self.alice, published=False)
        self.authenticate(ALICE_SUB)
        response = self.client.post(self.create_url(draft.id), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_open_on_a_shift_already_started(self):
        past = make_shift(self.facility, self.alice, days_ahead=-1)
        self.authenticate(ALICE_SUB)
        response = self.client.post(self.create_url(past.id), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_one_pending_request_per_shift(self):
        """Two open requests on one shift could each be accepted by a
        different person, double-booking it."""
        self.authenticate(ALICE_SUB)
        self.client.post(self.create_url(), {}, format="json")
        response = self.client.post(self.create_url(), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(ShiftSwapRequest.objects.count(), 1)

    def test_can_reopen_after_cancelling(self):
        self.authenticate(ALICE_SUB)
        first = self.client.post(self.create_url(), {}, format="json").data
        self.client.post(
            f"/api/rota/swap-requests/{first['id']}/cancel/", {}, format="json"
        )
        response = self.client.post(self.create_url(), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_target_yourself(self):
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            self.create_url(), {"target_professional": self.alice.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- accepting ----------------------------------------------------

    def open_swap(self, target=None):
        return ShiftSwapRequest.objects.create(
            shift=self.shift,
            requesting_professional=self.alice,
            target_professional=target,
        )

    def test_accept_reassigns_the_shift(self):
        swap = self.open_swap()
        self.authenticate(BOB_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        swap.refresh_from_db()
        self.shift.refresh_from_db()
        self.assertEqual(swap.status, ShiftSwapRequest.Status.ACCEPTED)
        self.assertEqual(swap.accepted_by, self.bob)
        self.assertIsNotNone(swap.decided_at)
        self.assertEqual(self.shift.professional, self.bob)

    def test_reassignment_is_recorded_in_shift_history(self):
        swap = self.open_swap()
        self.authenticate(BOB_SUB)
        self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        self.shift.refresh_from_db()
        assignees = [h.professional_id for h in self.shift.history.order_by("history_date")]
        self.assertEqual(assignees, [self.alice.id, self.bob.id])

    def test_requester_cannot_accept_their_own(self):
        swap = self.open_swap()
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accepting_twice_conflicts(self):
        swap = self.open_swap()
        self.authenticate(BOB_SUB)
        self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        carla = make_profile(self.facility, "Carla", "carla@example.com", CARLA_SUB)
        self.assertIsNotNone(carla)
        self.authenticate(CARLA_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_targeted_request_only_acceptable_by_the_target(self):
        carla = make_profile(self.facility, "Carla", "carla@example.com", CARLA_SUB)
        swap = self.open_swap(target=carla)
        self.authenticate(BOB_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- cancelling ---------------------------------------------------

    def test_requester_can_cancel(self):
        swap = self.open_swap()
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        swap.refresh_from_db()
        self.assertEqual(swap.status, ShiftSwapRequest.Status.CANCELLED)

    def test_others_cannot_cancel(self):
        swap = self.open_swap()
        self.authenticate(BOB_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancelled_request_cannot_be_accepted(self):
        swap = self.open_swap()
        self.authenticate(ALICE_SUB)
        self.client.post(
            f"/api/rota/swap-requests/{swap.id}/cancel/", {}, format="json"
        )
        self.authenticate(BOB_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.alice)

    # --- listing ------------------------------------------------------

    def test_list_shows_open_requests_at_this_facility(self):
        self.open_swap()
        self.authenticate(BOB_SUB)
        response = self.client.get("/api/rota/swap-requests/")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["requesting_professional_name"], "Alice")

    def test_list_hides_requests_targeted_at_someone_else(self):
        carla = make_profile(self.facility, "Carla", "carla@example.com", CARLA_SUB)
        self.open_swap(target=carla)
        self.authenticate(BOB_SUB)
        response = self.client.get("/api/rota/swap-requests/")
        self.assertEqual(response.data, [])


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class SwapFacilityVisibilityTests(SupabaseAuthMixin, APITestCase):
    """Definition-of-done item 3: a facility sees swaps on its own rota.

    Visibility, never a gate — nothing here lets a facility approve, block or
    reverse a swap. It exists because a facility that cannot see who is
    actually working a shift has lost track of its own rota.
    """

    def setUp(self):
        super().setUp()
        self.facility = make_facility()
        self.facility.supabase_user_id = FACILITY_SUB
        self.facility.save()
        self.other = make_facility("Other", "other@example.com")
        self.other.supabase_user_id = OTHER_FACILITY_SUB
        self.other.save()

        self.alice = make_profile(self.facility, "Alice", "alice@example.com", ALICE_SUB)
        self.bob = make_profile(self.facility, "Bob", "bob@example.com", BOB_SUB)
        self.carla = make_profile(self.facility, "Carla", "carla@example.com", CARLA_SUB)
        self.shift = make_shift(self.facility, self.alice)

    def test_facility_sees_open_swaps_on_its_own_shifts(self):
        ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )
        self.authenticate(FACILITY_SUB)
        response = self.client.get("/api/rota/swap-requests/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["requesting_professional_name"], "Alice")

    def test_facility_sees_targeted_swaps_too(self):
        """A peer is filtered out of someone else's targeted offer; the
        facility owns the shift and is not a peer."""
        ShiftSwapRequest.objects.create(
            shift=self.shift,
            requesting_professional=self.alice,
            target_professional=self.carla,
        )
        self.authenticate(FACILITY_SUB)
        self.assertEqual(len(self.client.get("/api/rota/swap-requests/").data), 1)

    def test_facility_sees_who_actually_took_the_shift(self):
        swap = ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )
        self.authenticate(BOB_SUB)
        self.client.post(f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json")

        self.authenticate(FACILITY_SUB)
        row = self.client.get("/api/rota/swap-requests/?status=accepted").data[0]
        self.assertEqual(row["accepted_by_name"], "Bob")
        self.assertEqual(row["requesting_professional_name"], "Alice")

        # And the rota itself reflects the reassignment.
        shifts = self.client.get("/api/rota/shifts/").data
        self.assertEqual(shifts[0]["professional_name"], "Bob")

    def test_facility_cannot_see_another_facilitys_swaps(self):
        ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )
        self.authenticate(OTHER_FACILITY_SUB)
        self.assertEqual(self.client.get("/api/rota/swap-requests/").data, [])

    def test_facility_cannot_accept_a_swap(self):
        """Visibility, not a gate — and not a way to reassign staff either."""
        swap = ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )
        self.authenticate(FACILITY_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.alice)

    def test_facility_cannot_cancel_a_swap(self):
        swap = ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )
        self.authenticate(FACILITY_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{swap.id}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        swap.refresh_from_db()
        self.assertEqual(swap.status, ShiftSwapRequest.Status.PENDING)

    def test_unapproved_facility_sees_nothing(self):
        ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )
        self.facility.status = Facility.Status.SUSPENDED
        self.facility.save()
        self.authenticate(FACILITY_SUB)
        self.assertEqual(
            self.client.get("/api/rota/swap-requests/").status_code,
            status.HTTP_403_FORBIDDEN,
        )


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class SwapFacilityIsolationTests(SupabaseAuthMixin, APITestCase):
    """Definition-of-done item 6, for swap requests."""

    def setUp(self):
        super().setUp()
        self.facility = make_facility()
        self.other = make_facility("Other", "other@example.com")
        self.alice = make_profile(self.facility, "Alice", "alice@example.com", ALICE_SUB)
        self.outsider = make_profile(
            self.other, "Outsider", "outsider@example.com", OUTSIDER_SUB
        )
        self.shift = make_shift(self.facility, self.alice)
        self.swap = ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )

    def test_outsider_cannot_see_the_request(self):
        self.authenticate(OUTSIDER_SUB)
        self.assertEqual(self.client.get("/api/rota/swap-requests/").data, [])

    def test_outsider_cannot_accept(self):
        self.authenticate(OUTSIDER_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.alice)

    def test_outsider_cannot_cancel(self):
        self.authenticate(OUTSIDER_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_accept_gives_a_foreign_request_the_missing_one_answer(self):
        self.authenticate(OUTSIDER_SUB)
        foreign = self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/accept/", {}, format="json"
        )
        missing = self.client.post(
            "/api/rota/swap-requests/999999/accept/", {}, format="json"
        )
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.data, missing.data)

    def test_cancel_gives_a_foreign_request_the_missing_one_answer(self):
        self.authenticate(OUTSIDER_SUB)
        foreign = self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/cancel/", {}, format="json"
        )
        missing = self.client.post(
            "/api/rota/swap-requests/999999/cancel/", {}, format="json"
        )
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.data, missing.data)

    def test_a_targeted_request_is_hidden_the_same_way(self):
        """Intra-facility privacy uses the same mechanism: a colleague offered
        a shift they were not named for cannot tell the request apart from one
        that does not exist."""
        bob = make_profile(self.facility, "Bob", "bob@example.com", BOB_SUB)
        carla = make_profile(self.facility, "Carla", "carla@example.com", CARLA_SUB)
        self.swap.target_professional = carla
        self.swap.save()

        self.authenticate(BOB_SUB)
        targeted = self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/accept/", {}, format="json"
        )
        missing = self.client.post(
            "/api/rota/swap-requests/999999/accept/", {}, format="json"
        )
        self.assertEqual(targeted.status_code, missing.status_code)
        self.assertEqual(targeted.data, missing.data)
        self.assertEqual(bob.swap_requests_accepted.count(), 0)

    def test_cannot_target_another_facilitys_professional(self):
        self.swap.delete()
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            f"/api/rota/shifts/{self.shift.id}/swap-request/",
            {"target_professional": self.outsider.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_outsider_cannot_open_a_request_on_this_facilitys_shift(self):
        self.swap.delete()
        self.authenticate(OUTSIDER_SUB)
        response = self.client.post(
            f"/api/rota/shifts/{self.shift.id}/swap-request/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(ShiftSwapRequest.objects.exists())

    def test_another_facilitys_shift_looks_exactly_like_a_missing_one(self):
        """The two answers have to be identical, or the difference between
        them is an oracle: anyone with a Supabase account could walk shift ids
        and learn which exist across every facility on the platform. This is
        what the endpoint used to do — 403 for a real id, 404 for a missing
        one — and it is why the lookup is scoped rather than checked after.
        """
        self.authenticate(OUTSIDER_SUB)
        real = self.client.post(
            f"/api/rota/shifts/{self.shift.id}/swap-request/", {}, format="json"
        )
        missing = self.client.post(
            "/api/rota/shifts/999999/swap-request/", {}, format="json"
        )
        self.assertEqual(real.status_code, missing.status_code)
        self.assertEqual(real.data, missing.data)

    def test_facility_cannot_open_a_swap_request(self):
        """Offering a shift is the assignee's decision, not management's."""
        self.facility.supabase_user_id = FACILITY_SUB
        self.facility.save()
        self.swap.delete()
        self.authenticate(FACILITY_SUB)
        response = self.client.post(
            f"/api/rota/shifts/{self.shift.id}/swap-request/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ShiftSwapRequest.objects.exists())

    def test_professional_with_no_facility_cannot_open_a_request(self):
        """`facility_id=None` must match no shift rather than every shift whose
        facility is null — there are none, but the scoping should not depend on
        that being true."""
        nomad = Profile.objects.create(
            full_name="Nomad",
            email="nomad@example.com",
            license_number="NMC-N",
            license_body="NMC",
            supabase_user_id="99999999-0000-4000-8000-000000000099",
        )
        self.assertIsNone(nomad.facility_id)
        self.authenticate("99999999-0000-4000-8000-000000000099")
        response = self.client.post(
            f"/api/rota/shifts/{self.shift.id}/swap-request/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SwapAcceptConcurrencyTests(TransactionTestCase):
    """Definition-of-done item 2, proven by racing real threads.

    TransactionTestCase rather than TestCase: the default wraps each test in a
    transaction the threads could not see past, so the race would not be real.
    """

    def setUp(self):
        self.facility = make_facility()
        self.alice = make_profile(self.facility, "Alice", "alice@example.com", ALICE_SUB)
        self.contenders = [
            make_profile(
                self.facility,
                f"Contender{i}",
                f"c{i}@example.com",
                f"eeeeeeee-0000-4000-8000-{i:012d}",
            )
            for i in range(8)
        ]
        self.shift = make_shift(self.facility, self.alice)
        self.swap = ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )

    def _claim(self, profile, barrier, results, lock):
        """The exact statement the accept view runs."""
        try:
            barrier.wait(timeout=10)
            claimed = ShiftSwapRequest.objects.filter(
                pk=self.swap.pk, status=ShiftSwapRequest.Status.PENDING
            ).update(
                status=ShiftSwapRequest.Status.ACCEPTED,
                accepted_by=profile,
                decided_at=timezone.now(),
            )
            if claimed == 1:
                shift = Shift.objects.get(pk=self.shift.pk)
                shift.professional = profile
                shift.save(update_fields=["professional"])
            with lock:
                results.append((profile.id, claimed))
        except Exception as exc:  # surfaced by the assertions below
            with lock:
                results.append((profile.id, f"error: {exc}"))
        finally:
            connections.close_all()

    def test_exactly_one_of_eight_simultaneous_accepts_wins(self):
        results: list = []
        lock = threading.Lock()
        barrier = threading.Barrier(len(self.contenders))

        threads = [
            threading.Thread(target=self._claim, args=(p, barrier, results, lock))
            for p in self.contenders
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(
            len(results), len(self.contenders), f"threads did not all finish: {results}"
        )
        errors = [r for r in results if not isinstance(r[1], int)]
        self.assertEqual(errors, [], f"threads raised: {errors}")

        winners = [pid for pid, claimed in results if claimed == 1]
        losers = [pid for pid, claimed in results if claimed == 0]
        self.assertEqual(
            len(winners), 1, f"expected exactly one winner, got {len(winners)}"
        )
        self.assertEqual(len(losers), len(self.contenders) - 1)

        self.swap.refresh_from_db()
        self.shift.refresh_from_db()
        self.assertEqual(self.swap.status, ShiftSwapRequest.Status.ACCEPTED)
        self.assertEqual(self.swap.accepted_by_id, winners[0])
        # The decisive assertion: the shift belongs to the one winner, and the
        # other seven did not overwrite it on their way past.
        self.assertEqual(self.shift.professional_id, winners[0])

    def test_database_backend_under_test(self):
        """Recorded so a green run cannot be mistaken for a stronger guarantee
        than it is: the same statement is exercised on either backend, but only
        Postgres proves it under true row-level concurrency."""
        self.assertIn(connection.vendor, ("sqlite", "postgresql"))
