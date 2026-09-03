"""Tests for staff-file parsing and the background bulk import."""

import io
from unittest import mock

from django.test import TestCase, override_settings
from openpyxl import Workbook

from facilities.importing import ImportFileError, parse_staff_file
from facilities.models import Facility
from facilities.tasks import run_bulk_import
from profiles.models import Profile

HEADER = "full_name,email,license_number,license_body,phone\n"


def csv_bytes(*rows):
    return (HEADER + "".join(rows)).encode()


def xlsx_bytes(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class ParseStaffFileTests(TestCase):
    def test_parses_csv(self):
        rows, headers = parse_staff_file(
            csv_bytes("Jane Doe,jane@example.com,NMC-1,NMC,07700900000\n"), "staff.csv"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["full_name"], "Jane Doe")
        self.assertEqual(rows[0]["email"], "jane@example.com")
        self.assertIn("license_number", headers)

    def test_parses_xlsx(self):
        content = xlsx_bytes(
            [
                ["Full Name", "Email", "Licence Number", "Licence Body", "Phone"],
                ["Ade Bello", "ade@example.com", "NMC-2", "NMC", "07700900001"],
            ]
        )
        rows, _ = parse_staff_file(content, "staff.xlsx")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["full_name"], "Ade Bello")
        self.assertEqual(rows[0]["license_number"], "NMC-2")

    def test_header_aliases_are_accepted(self):
        """Real spreadsheets say 'Full Name' and 'Licence Number', not our
        snake_case field names."""
        content = b"Full Name,E-Mail,Licence Number,Regulator\nA B,a@b.com,X1,NMC\n"
        rows, _ = parse_staff_file(content, "staff.csv")
        self.assertEqual(rows[0]["full_name"], "A B")
        self.assertEqual(rows[0]["email"], "a@b.com")
        self.assertEqual(rows[0]["license_body"], "NMC")

    def test_utf8_bom_is_stripped(self):
        """Excel writes a BOM; without stripping it the first header becomes
        '\\ufefffull_name' and the required-column check fails."""
        content = ("﻿" + HEADER + "A B,a@b.com,X1,NMC,\n").encode("utf-8")
        rows, _ = parse_staff_file(content, "staff.csv")
        self.assertEqual(rows[0]["full_name"], "A B")

    def test_blank_trailing_rows_ignored(self):
        content = csv_bytes("A B,a@b.com,X1,NMC,\n", ",,,,\n", ",,,,\n")
        rows, _ = parse_staff_file(content, "staff.csv")
        self.assertEqual(len(rows), 1)

    def test_missing_required_column_is_rejected(self):
        with self.assertRaises(ImportFileError) as ctx:
            parse_staff_file(b"full_name,email\nA B,a@b.com\n", "staff.csv")
        self.assertIn("license_number", str(ctx.exception))

    def test_every_alias_key_is_actually_reachable(self):
        """Aliases are looked up *after* normalisation, so a key containing a
        hyphen or capital could never match. This caught exactly that."""
        from facilities.importing import COLUMN_ALIASES, normalise_header

        unreachable = [
            key
            for key, target in COLUMN_ALIASES.items()
            if normalise_header(key) != target
        ]
        self.assertEqual(unreachable, [], f"Unreachable alias keys: {unreachable}")

    def test_unsupported_extension_rejected(self):
        with self.assertRaises(ImportFileError):
            parse_staff_file(b"whatever", "staff.pdf")

    def test_empty_file_rejected(self):
        with self.assertRaises(ImportFileError):
            parse_staff_file(b"", "staff.csv")


@override_settings(SUPABASE_SECRET_KEY="")
class BulkImportTaskTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            status=Facility.Status.APPROVED,
        )

    def test_creates_profiles_as_pending_bulk_import(self):
        content = csv_bytes(
            "Jane Doe,jane@example.com,NMC-1,NMC,07700900000\n",
            "Ade Bello,ade@example.com,NMC-2,NMC,07700900001\n",
        )
        report = run_bulk_import(self.facility.id, content, "staff.csv")

        self.assertEqual(report["created"], 2)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(Profile.objects.count(), 2)
        for profile in Profile.objects.all():
            self.assertEqual(profile.facility, self.facility)
            self.assertEqual(profile.onboarding_path, Profile.OnboardingPath.BULK_IMPORT)
            self.assertEqual(profile.verification_state, Profile.VerificationState.PENDING)
            self.assertEqual(profile.history.count(), 1)

    def test_one_bad_row_does_not_discard_the_rest(self):
        content = csv_bytes(
            "Jane Doe,jane@example.com,NMC-1,NMC,\n",
            "No Email,,NMC-2,NMC,\n",
            "Ade Bello,ade@example.com,NMC-3,NMC,\n",
        )
        report = run_bulk_import(self.facility.id, content, "staff.csv")

        self.assertEqual(report["created"], 2)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(Profile.objects.count(), 2)
        self.assertEqual(report["errors"][0]["row"], 3)

    def test_reimporting_skips_existing_people(self):
        content = csv_bytes("Jane Doe,jane@example.com,NMC-1,NMC,\n")
        run_bulk_import(self.facility.id, content, "staff.csv")
        report = run_bulk_import(self.facility.id, content, "staff.csv")

        self.assertEqual(report["created"], 0)
        self.assertEqual(report["skipped_existing"], 1)
        self.assertEqual(Profile.objects.count(), 1)

    def test_email_is_normalised_to_lowercase(self):
        content = csv_bytes("Jane Doe,JANE@Example.COM,NMC-1,NMC,\n")
        run_bulk_import(self.facility.id, content, "staff.csv")
        self.assertEqual(Profile.objects.get().email, "jane@example.com")

    def test_unparseable_file_reports_without_raising(self):
        report = run_bulk_import(self.facility.id, b"nonsense", "staff.pdf")
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["created"], 0)
        self.assertTrue(report["errors"])

    def test_missing_facility_is_reported(self):
        report = run_bulk_import(999999, csv_bytes(), "staff.csv")
        self.assertEqual(report["failed"], 1)
        self.assertIn("no longer exists", report["errors"][0]["error"])

    @override_settings(BULK_IMPORT_MAX_ROWS=2)
    def test_row_limit_enforced(self):
        content = csv_bytes(
            "A A,a@example.com,1,NMC,\n",
            "B B,b@example.com,2,NMC,\n",
            "C C,c@example.com,3,NMC,\n",
        )
        report = run_bulk_import(self.facility.id, content, "staff.csv")
        self.assertEqual(report["created"], 0)
        self.assertIn("row limit", report["errors"][0]["error"])
        self.assertEqual(Profile.objects.count(), 0)

    def test_no_accounts_provisioned_without_service_key(self):
        report = run_bulk_import(
            self.facility.id, csv_bytes("A A,a@example.com,1,NMC,\n"), "staff.csv"
        )
        self.assertFalse(report["accounts_configured"])
        self.assertEqual(report["accounts_provisioned"], 0)
        self.assertIsNone(Profile.objects.get().supabase_user_id)


@override_settings(SUPABASE_SECRET_KEY="sb_secret_test_key")
class BulkImportAccountProvisioningTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(
            name="Oakwood",
            registration_number="REG-1",
            contact_email="oak@example.com",
            status=Facility.Status.APPROVED,
        )

    @mock.patch("facilities.tasks.invite_user")
    def test_provisions_account_and_stores_supabase_id(self, invite):
        invite.return_value = "aaaaaaaa-1111-4222-8333-444444444444"
        report = run_bulk_import(
            self.facility.id, csv_bytes("A A,a@example.com,1,NMC,\n"), "staff.csv"
        )
        invite.assert_called_once_with("a@example.com")
        self.assertEqual(report["accounts_provisioned"], 1)
        self.assertEqual(
            str(Profile.objects.get().supabase_user_id),
            "aaaaaaaa-1111-4222-8333-444444444444",
        )

    @mock.patch("facilities.tasks.invite_user")
    def test_profile_still_created_when_provisioning_fails(self, invite):
        """A Supabase outage must not cost the facility their import — the
        licence still needs verifying and the account can come later."""
        from core.supabase_admin import SupabaseAdminError

        invite.side_effect = SupabaseAdminError("Supabase Auth returned 500")
        report = run_bulk_import(
            self.facility.id, csv_bytes("A A,a@example.com,1,NMC,\n"), "staff.csv"
        )
        self.assertEqual(report["created"], 1)
        self.assertEqual(report["accounts_provisioned"], 0)
        self.assertTrue(any("not provisioned" in e["error"] for e in report["errors"]))
        self.assertIsNone(Profile.objects.get().supabase_user_id)
