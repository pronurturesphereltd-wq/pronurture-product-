"""The role-matching guardrail on shift swap acceptance.

A patient-safety rule: a general nurse must not end up covering a shift that
needs an ENT Registrar. Enforced server-side on acceptance, never only hinted
at in the UI.
"""

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests import AUDIENCE, ISSUER, StubSigningKey, make_token
from facilities.models import Facility
from profiles.models import Profile
from rota.models import Shift, ShiftSwapRequest
from rota.roles import role_matches

ALICE_SUB = "aaaaaaaa-0000-4000-8000-000000000001"
BOB_SUB = "bbbbbbbb-0000-4000-8000-000000000002"
CARLA_SUB = "cccccccc-0000-4000-8000-000000000003"

REGISTRAR = "ENT Registrar"
NURSE = "General Nurse"


class RoleMatchingRuleTests(SimpleTestCase):
    """The rule on its own, without a request in the way."""

    def test_identical_roles_match(self):
        self.assertTrue(role_matches(REGISTRAR, REGISTRAR))

    def test_different_roles_do_not_match(self):
        self.assertFalse(role_matches(NURSE, REGISTRAR))

    def test_blank_designation_never_matches(self):
        for blank in ("", "   ", None):
            self.assertFalse(role_matches(blank, REGISTRAR))

    def test_blank_requirement_never_matches(self):
        for blank in ("", "   ", None):
            self.assertFalse(role_matches(REGISTRAR, blank))

    def test_blank_does_not_match_blank(self):
        """The trap in leaving this to plain string equality: two unset roles
        would compare equal and wave through every swap on the platform."""
        self.assertFalse(role_matches("", ""))
        self.assertFalse(role_matches(None, None))

    def test_case_and_spacing_do_not_change_the_role(self):
        """Deviation from a byte-exact reading of the spec, and deliberate.
        What "exact" rules out is semantic looseness — seniority tiers,
        specialties, close-enough. Two people typing the same job title with
        different capitals have not named different jobs, and blocking that
        swap would buy no safety while reading as a bug."""
        self.assertTrue(role_matches("ent registrar", "ENT Registrar"))
        self.assertTrue(role_matches("  ENT Registrar  ", "ENT Registrar"))
        self.assertTrue(role_matches("ENT  Registrar", "ENT Registrar"))

    def test_no_partial_or_hierarchical_matching(self):
        """Explicitly out of scope: seniority and specialty are a bigger design
        question and are not implied by this guardrail."""
        self.assertFalse(role_matches("Registrar", REGISTRAR))
        self.assertFalse(role_matches("ENT Registrar", "ENT Registrar (Senior)"))
        self.assertFalse(role_matches("Senior Nurse", NURSE))


@override_settings(
    SUPABASE_JWT_ISSUER=ISSUER,
    SUPABASE_JWT_AUDIENCE=AUDIENCE,
    SUPABASE_JWT_LEEWAY_SECONDS=10,
)
class SwapRoleGuardrailTests(APITestCase):
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
            status=Facility.Status.APPROVED,
        )
        # Alice holds an ENT Registrar shift and wants rid of it.
        self.alice = self.make_profile("Alice", "alice@example.com", ALICE_SUB, REGISTRAR)
        # Bob is designated for the same role and may cover it.
        self.bob = self.make_profile("Bob", "bob@example.com", BOB_SUB, REGISTRAR)
        # Carla is a nurse. She may not, however willing.
        self.carla = self.make_profile("Carla", "carla@example.com", CARLA_SUB, NURSE)

        start = timezone.now() + timedelta(days=2)
        self.shift = Shift.objects.create(
            facility=self.facility,
            professional=self.alice,
            role=REGISTRAR,
            ward="Ward 4",
            start_time=start,
            end_time=start + timedelta(hours=8),
            is_published=True,
            published_at=timezone.now(),
        )
        self.swap = ShiftSwapRequest.objects.create(
            shift=self.shift, requesting_professional=self.alice
        )

    def make_profile(self, name, email, sub, role):
        return Profile.objects.create(
            full_name=name,
            email=email,
            license_number=f"NMC-{name}",
            license_body="NMC",
            role=role,
            facility=self.facility,
            supabase_user_id=sub,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    def authenticate(self, sub):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(sub=sub)}")

    def accept(self, sub):
        self.authenticate(sub)
        return self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/accept/", {}, format="json"
        )

    def test_matching_role_can_accept(self):
        response = self.accept(BOB_SUB)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.bob)
        self.swap.refresh_from_db()
        self.assertEqual(self.swap.status, ShiftSwapRequest.Status.ACCEPTED)

    def test_mismatched_role_is_refused(self):
        response = self.accept(CARLA_SUB)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(REGISTRAR, response.data["detail"])
        self.assertIn(NURSE, response.data["detail"])

    def test_refusal_is_400_not_403_or_404(self):
        """A validation failure, not an authorisation or existence question.
        The enumeration-safe 404s elsewhere in this view hide whether a row
        exists; Carla can see this request perfectly well and is entitled to
        know exactly why she cannot take it."""
        response = self.accept(CARLA_SUB)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(response.status_code, (401, 403, 404))

    def test_blank_role_is_refused(self):
        self.carla.role = ""
        self.carla.save()
        response = self.accept(CARLA_SUB)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Says what to do about it rather than reporting a designation of ''.
        self.assertIn("no designated role", response.data["detail"])
        self.assertIn("Ask your facility", response.data["detail"])

    def test_blank_role_cannot_accept_a_blank_role_shift(self):
        """Two unset roles must not compare equal and wave the swap through."""
        self.carla.role = ""
        self.carla.save()
        self.shift.role = ""
        self.shift.save()

        self.assertEqual(self.accept(CARLA_SUB).status_code, 400)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.alice)

    # --- the ordering requirement -------------------------------------

    def test_refusal_leaves_the_request_untouched(self):
        """The claim is a one-way door. A mismatched attempt has to bounce
        before it, or the request is burnt and nobody can take the shift."""
        self.accept(CARLA_SUB)

        self.swap.refresh_from_db()
        self.assertEqual(self.swap.status, ShiftSwapRequest.Status.PENDING)
        self.assertIsNone(self.swap.accepted_by)
        self.assertIsNone(self.swap.decided_at)

        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.alice)

    def test_a_matched_professional_can_still_accept_afterwards(self):
        """The point of checking first, stated as the outcome that matters."""
        self.assertEqual(self.accept(CARLA_SUB).status_code, 400)
        self.assertEqual(self.accept(BOB_SUB).status_code, 200)

        self.shift.refresh_from_db()
        self.assertEqual(self.shift.professional, self.bob)

    def test_refusal_writes_no_history(self):
        """A rejected attempt is not an event in the swap's life."""
        before = self.swap.history.count()
        self.accept(CARLA_SUB)
        self.assertEqual(self.swap.history.count(), before)

    def test_case_difference_does_not_block_a_legitimate_swap(self):
        self.shift.role = "ent registrar"
        self.shift.save()
        self.assertEqual(self.accept(BOB_SUB).status_code, status.HTTP_200_OK)

    # --- scope limits, asserted so they are not widened by accident ----

    def test_the_guardrail_does_not_gate_initial_assignment(self):
        """A facility can still roster anyone onto any shift. Only swaps are
        gated — enforcing this at assignment is a separate, later decision."""
        self.facility.supabase_user_id = "f0000000-0000-4000-8000-000000000010"
        self.facility.save()
        self.authenticate("f0000000-0000-4000-8000-000000000010")

        start = timezone.now() + timedelta(days=5)
        response = self.client.post(
            "/api/rota/shifts/",
            {
                "role": REGISTRAR,
                "professional": self.carla.id,  # a nurse
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(hours=8)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["professional"], self.carla.id)

    def test_ward_does_not_gate_anything(self):
        """Informational only, by design. Bob's profile carries no ward at all
        and the swap still goes through."""
        self.shift.ward = "Ward 9"
        self.shift.save()
        self.assertEqual(self.accept(BOB_SUB).status_code, status.HTTP_200_OK)

    def test_cancel_is_not_gated_by_role(self):
        """The guardrail is about who may take a shift on, not who may
        withdraw an offer. Alice can always retract her own."""
        self.authenticate(ALICE_SUB)
        response = self.client.post(
            f"/api/rota/swap-requests/{self.swap.id}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
