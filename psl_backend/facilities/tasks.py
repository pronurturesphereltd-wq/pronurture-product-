"""Background bulk import.

Runs under django-q2 rather than in the request, because a large facility's
staff list means one Supabase Auth call per row and would otherwise time out
the HTTP request.

The unit of failure is the row, not the file. One bad email address should not
discard the other 200 people, so each row is committed independently and the
task returns a report describing exactly what happened.
"""

import logging

from django.conf import settings
from django.db import transaction

from core.supabase_admin import SupabaseAdminError, invite_user, is_configured
from facilities.importing import ImportFileError, parse_staff_file
from facilities.models import Facility
from profiles.models import Profile

logger = logging.getLogger(__name__)


def run_bulk_import(facility_id, content, filename):
    """Parse `content` and create a Profile per row, provisioning auth accounts.

    Returns a report dict. django-q2 persists it on the Task record, so the
    outcome is visible in Django Admin without inventing a new model.
    """
    report = {
        "facility_id": facility_id,
        "filename": filename,
        "created": 0,
        "skipped_existing": 0,
        "failed": 0,
        "accounts_provisioned": 0,
        "errors": [],
        "accounts_configured": is_configured(),
    }

    try:
        facility = Facility.objects.get(pk=facility_id)
    except Facility.DoesNotExist:
        report["errors"].append({"row": None, "error": "Facility no longer exists."})
        report["failed"] = 1
        return report

    try:
        rows, _headers = parse_staff_file(content, filename)
    except ImportFileError as exc:
        report["errors"].append({"row": None, "error": str(exc)})
        report["failed"] = 1
        return report

    if len(rows) > settings.BULK_IMPORT_MAX_ROWS:
        report["errors"].append(
            {
                "row": None,
                "error": (
                    f"File has {len(rows)} rows, over the "
                    f"{settings.BULK_IMPORT_MAX_ROWS} row limit."
                ),
            }
        )
        report["failed"] = 1
        return report

    report["total_rows"] = len(rows)

    for index, row in enumerate(rows, start=2):  # row 1 is the header
        outcome = _import_one(facility, row, index, report)
        if outcome == "created":
            report["created"] += 1
        elif outcome == "skipped":
            report["skipped_existing"] += 1
        else:
            report["failed"] += 1

    return report


def _import_one(facility, row, row_number, report):
    email = (row.get("email") or "").strip().lower()
    full_name = (row.get("full_name") or "").strip()

    if not email or "@" not in email:
        report["errors"].append(
            {"row": row_number, "error": f"Invalid or missing email: {email or '(blank)'}"}
        )
        return "failed"
    if not full_name:
        report["errors"].append({"row": row_number, "error": "Missing full_name"})
        return "failed"

    if Profile.objects.filter(email__iexact=email).exists():
        # Re-importing a staff list is normal; existing people are left alone
        # rather than duplicated or silently overwritten.
        return "skipped"

    supabase_user_id = None
    if is_configured():
        try:
            supabase_user_id = invite_user(email)
            report["accounts_provisioned"] += 1
        except SupabaseAdminError as exc:
            # The Profile is still worth creating: PSL staff can verify the
            # licence, and the account can be provisioned later.
            report["errors"].append(
                {"row": row_number, "error": f"Account not provisioned: {exc}"}
            )

    try:
        with transaction.atomic():
            Profile.objects.create(
                full_name=full_name,
                email=email,
                phone=(row.get("phone") or "").strip(),
                license_number=(row.get("license_number") or "").strip(),
                license_body=(row.get("license_body") or "").strip(),
                facility=facility,
                supabase_user_id=supabase_user_id,
                onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
                verification_state=Profile.VerificationState.PENDING,
            )
    except Exception as exc:
        logger.exception("Bulk import row %s failed", row_number)
        report["errors"].append({"row": row_number, "error": str(exc)[:300]})
        return "failed"

    return "created"
