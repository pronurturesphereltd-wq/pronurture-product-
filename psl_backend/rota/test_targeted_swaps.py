"""Targeted-only swap offers.

Every offer names a colleague. The behaviour this exists to produce is a
negative one: a qualified colleague at the same facility who was *not* named
must not see the offer, and must not be able to take it even by calling the
API directly with the right id.
"""

from datetime import timedelta
from unittest import mock

from django.test import override_settings
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

ROLE = "A&E Nurse"
OTHER_ROLE = "Midwife"


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class TargetedSwapBase(APITestCase):
    def setUp(self):
        patcher = mock.patch(
            "core.authentication.get_jwk_client",
            return_value=mock.Mock(
                get_signing_key_from_jwt=mock.Mock(return_value=StubSigningKey())
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.facility = self.make_facility("Oakwood", "oak@example.com", FACILITY_SUB)
        self.other_facility = self.make_facility("Ivy", "ivy@example.com", None)

        # Alice holds the shift. Bob and Carla are both designated for it —
        # Carla is the third qualified colleague the addendum asks for, and
        # she must never see an offer aimed at Bob.
        self.alice = self.make_profile("Alice", "alice@example.com", ALICE_SUB, ROLE)
        self.bob = self.make_profile("Bob", "bob@example.com", BOB_SUB, ROLE)
        self.carla = self.make_profile("Carla", "carla@example.com", CARLA_SUB, ROLE)
        self.outsider = self.make_profile(
            "Outsider",
            "outsider@example.com",
            OUTSIDER_SUB,
            ROLE,
            facility=self.other_facility,
        )

        start = timezone.now() + timedelta(days=3)
        self.shift = Shift.objects.create(
            facility=self.facility,
            professional=self.alice,
            role=ROLE,
            ward="Ward 4",
            start_time=start,
            end_time=start + timedelta(hours=8),
            is_published=True,
            published_at=timezone.now(),
        )

    def make_facility(self, name, email, sub):
        return Facility.objects.create(
            name=name,
            registration_number=f"REG-{name}",
            contact_email=email,
            supabase_user_id=sub,
            status=Facility.Status.APPROVED,
        )

    def make_profile(self, name, email, sub, role, facility=None):
        return Profile.objects.create(
            full_name=name,
            email=email,
            license_number=f"NMC-{name}",
            license_body="NMC",
            role=role,
            facility=facility or self.facility,
            supabase_user_id=sub,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    def authenticate(self, sub):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(sub=sub)}")

    def offer_to(self, target, sub=ALICE_SUB):
        self.authenticate(sub)
        return self.client.post(
            f"/api/rota/shifts/{self.shift.id}/swap-request/",
            {"target_professional": target.id},
            format="json",
        )

    def eligible(self, sub=ALICE_SUB, shift_id=None):
        self.authenticate(sub)
        return self.client.get(
            f"/api/rota/shifts/{shift_id or self.shift.id}/eligible-colleagues/"
        )


class EligibleColleaguesTests(TargetedSwapBase):
    def test_lists_colleagues_with_a_matching_role(self):
        response = self.eligible()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            sorted(row["full_name"] for row in response.data), ["Bob", "Carla"]
        )

    def test_excludes_the_requester(self):
        names = [row["full_name"] for row in self.eligible().data]
        self.assertNotIn("Alice", names)

    def test_excludes_a_mismatched_role(self):
        self.carla.role = OTHER_ROLE
        self.carla.save()
        names = [row["full_name"] for row in self.eligible().data]
        self.assertEqual(names, ["Bob"])

    def test_excludes_a_colleague_with_no_role_set(self):
        self.carla.role = ""
        self.carla.save()
        names = [row["full_name"] for row in self.eligible().data]
        self.assertEqual(names, ["Bob"])

    def test_matching_is_the_same_rule_the_guardrail_uses(self):
        """Case and spacing, so the list cannot disagree with acceptance."""
        self.bob.role = "  a&e  NURSE "
        self.bob.save()
        names = [row["full_name"] for row in self.eligible().data]
        self.assertIn("Bob", names)

    def test_excludes_another_facilitys_staff(self):
        names = [row["full_name"] for row in self.eligible().data]
        self.assertNotIn("Outsider", names)

    def test_empty_when_nobody_qualifies(self):
        """The case the UI has to handle: a dropdown with nothing in it looks
        broken, so the frontend needs to be able to tell."""
        self.bob.role = OTHER_ROLE
        self.bob.save()
        self.carla.role = OTHER_ROLE
        self.carla.save()

        response = self.eligible()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_withholds_licence_and_phone_details(self):
        """A colleague is not the facility. They get enough to pick a person."""
        row = self.eligible().data[0]
        for withheld in ("license_number", "license_body", "phone"):
            self.assertNotIn(withheld, row)

    def test_only_the_assignee_may_ask(self):
        response = self.eligible(sub=BOB_SUB)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_another_facility_gets_the_missing_shift_answer(self):
        """Same enumeration-safety rule as everywhere else."""
        self.authenticate(OUTSIDER_SUB)
        foreign = self.client.get(
            f"/api/rota/shifts/{self.shift.id}/eligible-colleagues/"
        )
        missing = self.client.get("/api/rota/shifts/999999/eligible-colleagues/")

        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.data, missing.data)

    def test_a_facility_token_is_refused(self):
        """Professional-safe by design — /api/facilities/staff/ is the
        facility's version and answers 403 for a professional."""
        self.authenticate(FACILITY_SUB)
        response = self.client.get(
            f"/api/rota/shifts/{self.shift.id}/eligible-colleagues/"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        self.client.credentials()
        response = self.client.get(
            f"/api/rota/shifts/{self.shift.id}/eligible-colleagues/"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TargetedOfferTests(TargetedSwapBase):
    def test_offer_requires_a_target(self):
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            f"/api/rota/shifts/{self.shift.id}/swap-request/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("choose a colleague", str(response.data))

    def test_offer_to_a_named_colleague_succeeds(self):
        response = self.offer_to(self.bob)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        swap = ShiftSwapRequest.objects.get()
        self.assertEqual(swap.target_professional, self.bob)

    def test_cannot_offer_across_facilities(self):
        response = self.offer_to(self.outsider)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ShiftSwapRequest.objects.exists())

    def test_cannot_offer_to_a_mismatched_role(self):
        self.bob.role = OTHER_ROLE
        self.bob.save()
        response = self.offer_to(self.bob)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ShiftSwapRequest.objects.exists())

    def test_cannot_offer_to_someone_with_no_role(self):
        self.bob.role = ""
        self.bob.save()
        self.assertEqual(
            self.offer_to(self.bob).status_code, status.HTTP_400_BAD_REQUEST
        )

    def test_cannot_offer_to_yourself(self):
        self.assertEqual(
            self.offer_to(self.alice).status_code, status.HTTP_400_BAD_REQUEST
        )


class TargetedVisibilityTests(TargetedSwapBase):
    """Done-criterion 2, stated as the negative it exists to produce."""

    def setUp(self):
        super().setUp()
        self.offer_to(self.bob)
        self.swap = ShiftSwapRequest.objects.get()

    def test_the_named_colleague_sees_it(self):
        self.authenticate(BOB_SUB)
        rows = self.client.get("/api/rota/swap-requests/").data

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_professional_name"], "Bob")

    def test_a_qualified_colleague_who_was_not_named_sees_nothing(self):
        """Carla is at the same facility with the same role. Before targeting
        she could have taken this shift; the entire point of the change is
        that she can no longer even see it."""
        self.authenticate(CARLA_SUB)
        self.assertEqual(self.client.get("/api/rota/swap-requests/").data, [])

    def test_the_requester_still_sees_it(self):
        self.authenticate(ALICE_SUB)
        rows = self.client.get("/api/rota/swap-requests/").data
        self.assertEqual(len(rows), 1)

    def test_another_facility_sees_nothing(self):
        self.authenticate(OUTSIDER_SUB)
        self.assertEqual(self.client.get("/api/rota/swap-requests/").data, [])

    def test_the_facility_still_sees_every_offer_on_its_rota(self):
        """Unchanged: a visibility-only stakeholder, not a participant."""
        self.authenticate(FACILITY_SUB)
        rows = self.client.get("/api/rota/swap-requests/").data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_professional_name"], "Bob")


class TargetedAcceptanceTests(TargetedSwapBase):
    """Done-criterion 3: the API refuses, not merely the UI."""

    def setUp(self):
        super().setUp()
        self.offer_to(self.bob)
        self.swap = ShiftSwapRequest.objects.get()

    def accept_as(self, sub):
        self.authenticate(sub)
        return self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/accept/", {}, format="json"
        )

    def test_the_named_colleague_can_accept(self):
        response = self.accept_as(BOB_SUB)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.bob)

    def test_a_qualified_colleague_who_was_not_named_cannot_accept(self):
        """Calling the API directly with the correct id, not clicking a button
        that is not there."""
        response = self.accept_as(CARLA_SUB)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.alice)
        self.swap.refresh_from_db()
        self.assertEqual(self.swap.status, ShiftSwapRequest.Status.PENDING)

    def test_the_refusal_looks_like_a_missing_request(self):
        """Carla learns nothing about whether the offer exists."""
        self.authenticate(CARLA_SUB)
        real = self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/accept/", {}, format="json"
        )
        missing = self.client.post(
            "/api/rota/swap-requests/999999/accept/", {}, format="json"
        )
        self.assertEqual(real.status_code, missing.status_code)
        self.assertEqual(real.data, missing.data)

    def test_another_facility_cannot_accept(self):
        self.assertEqual(
            self.accept_as(OUTSIDER_SUB).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_the_requester_cannot_accept_their_own(self):
        response = self.accept_as(ALICE_SUB)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retargeting_after_a_withdrawal_works(self):
        """Alice changes her mind about who to ask."""
        self.authenticate(ALICE_SUB)
        self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/cancel/", {}, format="json"
        )
        self.assertEqual(self.offer_to(self.carla).status_code, status.HTTP_201_CREATED)

        new_swap = ShiftSwapRequest.objects.get(
            status=ShiftSwapRequest.Status.PENDING
        )
        self.authenticate(CARLA_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{new_swap.id}/accept/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
