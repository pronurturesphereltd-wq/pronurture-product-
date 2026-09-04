"""The provision_professional_account management command."""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.supabase_admin import SupabaseAdminError
from facilities.models import Facility
from profiles.management.commands.provision_professional_account import (
    generate_password,
)
from profiles.models import Profile

EXISTING_SUB = "11111111-0000-4000-8000-000000000001"
NEW_SUB = "22222222-0000-4000-8000-000000000002"


def run(*args, **kwargs):
    out = StringIO()
    call_command("provision_professional_account", *args, stdout=out, stderr=out, **kwargs)
    return out.getvalue()


@override_settings(SUPABASE_SECRET_KEY="sb_secret_test")
class ProvisionAccountTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            status=Facility.Status.APPROVED,
        )
        self.profile = Profile.objects.create(
            full_name="Bola Nurse",
            email="bola@example.com",
            license_number="NMC-2",
            license_body="NMC",
            facility=self.facility,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        return_value=NEW_SUB,
    )
    def test_links_the_account_and_sets_the_role(self, create_user):
        output = run("bola@example.com", "--role", "A&E Nurse")

        self.profile.refresh_from_db()
        self.assertEqual(str(self.profile.supabase_user_id), NEW_SUB)
        self.assertEqual(self.profile.role, "A&E Nurse")
        self.assertIn("Linked Bola Nurse", output)
        self.assertIn(NEW_SUB, output)

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        return_value=NEW_SUB,
    )
    def test_generated_password_is_printed_once(self, create_user):
        """It is stored nowhere, so if it is not shown here it is lost."""
        output = run("bola@example.com")
        sent_password = create_user.call_args.args[1]

        self.assertIn(sent_password, output)
        self.assertGreaterEqual(len(sent_password), 20)

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        return_value=NEW_SUB,
    )
    def test_supplied_password_is_used(self, create_user):
        run("bola@example.com", "--password", "chosen-one")
        self.assertEqual(create_user.call_args.args[1], "chosen-one")

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        return_value=NEW_SUB,
    )
    def test_the_link_reaches_history(self, create_user):
        """save(), not queryset.update() — the same rule as everywhere else."""
        run("bola@example.com", "--role", "A&E Nurse")
        latest = self.profile.history.order_by("-history_date").first()
        self.assertEqual(str(latest.supabase_user_id), NEW_SUB)
        self.assertEqual(latest.role, "A&E Nurse")

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        return_value=NEW_SUB,
    )
    def test_warns_when_no_role_is_set(self, create_user):
        """A blank role blocks every swap, which looks like a bug unless the
        command says so at the moment it matters."""
        output = run("bola@example.com")
        self.assertIn("cannot accept any shift swap", output)

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user"
    )
    def test_matching_is_case_insensitive(self, create_user):
        create_user.return_value = NEW_SUB
        run("BOLA@EXAMPLE.COM")
        self.profile.refresh_from_db()
        self.assertEqual(str(self.profile.supabase_user_id), NEW_SUB)

    # --- refusals -----------------------------------------------------

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user"
    )
    def test_unknown_email_is_refused_without_calling_supabase(self, create_user):
        """It links an account to a profile that exists; it does not invent
        one. Creating an orphan auth account would be the worse outcome."""
        with self.assertRaises(CommandError) as ctx:
            run("nobody@example.com")
        self.assertIn("No profile with email", str(ctx.exception))
        create_user.assert_not_called()

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user"
    )
    def test_already_linked_profile_is_refused(self, create_user):
        self.profile.supabase_user_id = EXISTING_SUB
        self.profile.save()

        with self.assertRaises(CommandError) as ctx:
            run("bola@example.com")
        self.assertIn("already has supabase_user_id", str(ctx.exception))
        create_user.assert_not_called()

    @override_settings(SUPABASE_SECRET_KEY="")
    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user"
    )
    def test_missing_secret_key_is_refused_early(self, create_user):
        with self.assertRaises(CommandError):
            run("bola@example.com")
        create_user.assert_not_called()

    @mock.patch(
        "profiles.management.commands.provision_professional_account.find_user_by_email",
        return_value=EXISTING_SUB,
    )
    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        side_effect=SupabaseAdminError("422 email already registered"),
    )
    def test_adopts_an_existing_account_rather_than_demanding_cleanup(
        self, create_user, find_user
    ):
        """The ordinary case when an earlier attempt half-succeeded: the auth
        account exists but nothing links it here."""
        output = run("bola@example.com", "--role", "A&E Nurse")

        self.profile.refresh_from_db()
        self.assertEqual(str(self.profile.supabase_user_id), EXISTING_SUB)
        self.assertEqual(self.profile.role, "A&E Nurse")
        self.assertIn("Found existing account", output)
        # The password was not applied, so printing it would be a lie.
        self.assertIn("was NOT applied", output)

    @mock.patch(
        "profiles.management.commands.provision_professional_account.find_user_by_email",
        return_value=None,
    )
    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        side_effect=SupabaseAdminError("503 upstream unavailable"),
    )
    def test_failure_with_no_existing_account_changes_nothing(
        self, create_user, find_user
    ):
        with self.assertRaises(CommandError) as ctx:
            run("bola@example.com", "--role", "A&E Nurse")
        self.assertIn("Nothing was changed", str(ctx.exception))

        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.supabase_user_id)
        self.assertEqual(self.profile.role, "")

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user",
        return_value=EXISTING_SUB,
    )
    def test_an_id_belonging_to_another_profile_is_refused(self, create_user):
        """supabase_user_id is unique. Two profiles sharing one identity would
        make the permission lookup ambiguous."""
        Profile.objects.create(
            full_name="Amaka Nurse",
            email="amaka@example.com",
            license_number="NMC-1",
            license_body="NMC",
            facility=self.facility,
            supabase_user_id=EXISTING_SUB,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )
        with self.assertRaises(CommandError) as ctx:
            run("bola@example.com")
        self.assertIn("already linked to another profile", str(ctx.exception))

        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.supabase_user_id)


class PasswordGenerationTests(TestCase):
    def test_passwords_are_long_and_distinct(self):
        passwords = {generate_password() for _ in range(50)}
        self.assertEqual(len(passwords), 50)
        self.assertTrue(all(len(p) == 20 for p in passwords))

    def test_omits_glyphs_that_are_read_wrong(self):
        """These get retyped off a screen; l/1 and O/0 are where that fails."""
        combined = "".join(generate_password() for _ in range(50))
        for ambiguous in "lI1O0":
            self.assertNotIn(ambiguous, combined)


@override_settings(SUPABASE_SECRET_KEY="sb_secret_test")
class ResetPasswordTests(TestCase):
    """--reset-password: for an account that exists but cannot sign in.

    An invited professional who never opened the email has no password and an
    unconfirmed address. Both failures present as `invalid_credentials`, so
    the account looks fine from the outside and the profile looks correctly
    linked. Nothing short of this gets them in.
    """

    def setUp(self):
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            status=Facility.Status.APPROVED,
        )
        self.profile = Profile.objects.create(
            full_name="Amaka Nurse",
            email="amaka@example.com",
            license_number="NMC-1",
            license_body="NMC",
            role="A&E Nurse",
            facility=self.facility,
            supabase_user_id=EXISTING_SUB,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )

    @mock.patch(
        "profiles.management.commands.provision_professional_account.set_user_password",
        return_value=EXISTING_SUB,
    )
    def test_sets_a_password_on_the_linked_account(self, set_password):
        output = run("amaka@example.com", "--reset-password")

        set_password.assert_called_once()
        user_id, password = set_password.call_args.args
        self.assertEqual(str(user_id), EXISTING_SUB)
        self.assertIn(password, output)
        self.assertIn("Reset the password", output)

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user"
    )
    @mock.patch(
        "profiles.management.commands.provision_professional_account.set_user_password",
        return_value=EXISTING_SUB,
    )
    def test_does_not_create_a_second_account(self, set_password, create_user):
        """The link must survive: creating a new account would orphan the
        original and break any history already written against it."""
        run("amaka@example.com", "--reset-password")

        create_user.assert_not_called()
        self.profile.refresh_from_db()
        self.assertEqual(str(self.profile.supabase_user_id), EXISTING_SUB)

    @mock.patch(
        "profiles.management.commands.provision_professional_account.set_user_password",
        return_value=EXISTING_SUB,
    )
    def test_can_set_the_role_at_the_same_time(self, set_password):
        self.profile.role = ""
        self.profile.save()
        run("amaka@example.com", "--reset-password", "--role", "A&E Nurse")

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.role, "A&E Nurse")

    @mock.patch(
        "profiles.management.commands.provision_professional_account.set_user_password"
    )
    def test_refused_when_the_profile_has_no_account(self, set_password):
        unlinked = Profile.objects.create(
            full_name="Chidi Nurse",
            email="chidi@example.com",
            license_number="NMC-3",
            license_body="NMC",
            facility=self.facility,
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
        )
        with self.assertRaises(CommandError) as ctx:
            run(unlinked.email, "--reset-password")
        self.assertIn("no linked account", str(ctx.exception))
        set_password.assert_not_called()

    @mock.patch(
        "profiles.management.commands.provision_professional_account.set_user_password",
        side_effect=SupabaseAdminError("500 upstream"),
    )
    def test_failure_does_not_claim_success(self, set_password):
        with self.assertRaises(CommandError) as ctx:
            run("amaka@example.com", "--reset-password")
        self.assertIn("Could not set the password", str(ctx.exception))

    @mock.patch(
        "profiles.management.commands.provision_professional_account.create_user"
    )
    def test_a_linked_profile_without_the_flag_is_still_refused(self, create_user):
        """The guard that sent us here: without --reset-password this refuses
        rather than silently provisioning a duplicate."""
        with self.assertRaises(CommandError) as ctx:
            run("amaka@example.com")
        self.assertIn("--reset-password", str(ctx.exception))
        create_user.assert_not_called()
