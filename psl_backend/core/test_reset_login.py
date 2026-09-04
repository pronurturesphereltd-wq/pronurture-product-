"""reset_login_password — works for either kind of PSL account."""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.supabase_admin import SupabaseAdminError
from facilities.models import Facility
from profiles.models import Profile

FACILITY_SUB = "5d091b46-0000-4000-8000-00000000f001"
PROFESSIONAL_SUB = "aaaaaaaa-0000-4000-8000-000000000001"
ORPHAN_SUB = "00000000-0000-4000-8000-000000000000"

WHERE = "core.management.commands.reset_login_password"


def run(*args):
    out = StringIO()
    call_command("reset_login_password", *args, stdout=out, stderr=out)
    return out.getvalue()


@override_settings(SUPABASE_SECRET_KEY="sb_secret_test")
class ResetLoginPasswordTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            # Deliberately different from the login address. This is the real
            # shape of the data and the reason lookup goes through Supabase.
            contact_email="sample.home@example.com",
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

    @mock.patch(f"{WHERE}.set_user_password")
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=FACILITY_SUB)
    def test_resets_a_facility_login(self, find, set_password):
        output = run("owner@example.com")

        set_password.assert_called_once()
        self.assertEqual(str(set_password.call_args.args[0]), FACILITY_SUB)
        self.assertIn("Oakwood", output)
        self.assertIn(set_password.call_args.args[1], output)

    @mock.patch(f"{WHERE}.set_user_password")
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=FACILITY_SUB)
    def test_finds_the_facility_despite_a_different_contact_email(
        self, find, set_password
    ):
        """The trap this command exists to avoid: the facility's stored
        contact_email is not the address it signs in with, so any lookup keyed
        on the PSL record would find nothing."""
        run("owner@example.com")

        self.assertNotEqual(self.facility.contact_email, "owner@example.com")
        find.assert_called_once_with("owner@example.com")

    @mock.patch(f"{WHERE}.set_user_password")
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=PROFESSIONAL_SUB)
    def test_resets_a_professional_login(self, find, set_password):
        output = run("amaka@example.com")

        self.assertIn("Amaka Nurse", output)
        self.assertIn("A&E Nurse", output)

    @mock.patch(f"{WHERE}.set_user_password")
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=ORPHAN_SUB)
    def test_warns_when_nothing_claims_the_account(self, find, set_password):
        """The password will work and every endpoint will still refuse it."""
        output = run("stranger@example.com")

        set_password.assert_called_once()
        self.assertIn("No facility or profile is linked", output)

    @mock.patch(f"{WHERE}.set_user_password")
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=FACILITY_SUB)
    def test_warns_when_the_facility_is_not_approved(self, find, set_password):
        self.facility.status = Facility.Status.PENDING
        self.facility.save()
        output = run("owner@example.com")

        self.assertIn("not approved", output)

    @mock.patch(f"{WHERE}.set_user_password")
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=FACILITY_SUB)
    def test_supplied_password_is_used(self, find, set_password):
        run("owner@example.com", "--password", "chosen-one")
        self.assertEqual(set_password.call_args.args[1], "chosen-one")

    @mock.patch(f"{WHERE}.set_user_password")
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=None)
    def test_unknown_login_changes_nothing(self, find, set_password):
        with self.assertRaises(CommandError) as ctx:
            run("nobody@example.com")
        self.assertIn("No Supabase Auth account", str(ctx.exception))
        set_password.assert_not_called()

    @mock.patch(
        f"{WHERE}.set_user_password",
        side_effect=SupabaseAdminError("500 upstream"),
    )
    @mock.patch(f"{WHERE}.find_user_by_email", return_value=FACILITY_SUB)
    def test_failure_does_not_claim_success(self, find, set_password):
        with self.assertRaises(CommandError) as ctx:
            run("owner@example.com")
        self.assertIn("Could not set the password", str(ctx.exception))

    @override_settings(SUPABASE_SECRET_KEY="")
    @mock.patch(f"{WHERE}.find_user_by_email")
    def test_missing_secret_key_is_refused_early(self, find):
        with self.assertRaises(CommandError):
            run("owner@example.com")
        find.assert_not_called()
