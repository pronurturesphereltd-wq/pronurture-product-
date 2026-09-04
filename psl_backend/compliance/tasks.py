"""The scheduled compliance sweep.

Runs daily under django-q2. Licence expiry is a date, not a moment, so this
does not need the 15-minute cadence the shift reminders do.

Idempotency is the same concern as the reminder sweep, solved differently:
there is no per-row flag to claim, so "already flagged" is expressed as the
absence of an open alert of that type for that profile. The `.exclude()` below
is the readable half of that guard and the partial unique constraint on
ComplianceAlert is the enforced half — a run racing itself cannot produce two
open alerts for the same profile.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from compliance.models import ComplianceAlert
from profiles.models import Profile

logger = logging.getLogger(__name__)


def check_compliance():
    """Raise a ComplianceAlert for every licence expiring inside the lead time.

    Returns a report dict; django-q2 persists it on the Task row, so the
    outcome of any given run is readable in Django Admin afterwards.
    """
    lead_days = settings.COMPLIANCE_LICENSE_LEAD_DAYS
    threshold = timezone.now().date() + timedelta(days=lead_days)

    # `lte` deliberately includes dates already in the past: a licence that
    # expired last week is more of a compliance problem than one expiring next
    # month, not less.
    expiring = Profile.objects.filter(
        license_expiry_date__isnull=False,
        license_expiry_date__lte=threshold,
    ).exclude(
        # Both conditions in one exclude() so they must match the *same* alert
        # row. Split across two calls, a profile with a resolved licence alert
        # and an open alert of some other type would be wrongly skipped.
        compliance_alerts__alert_type=ComplianceAlert.AlertType.LICENSE_EXPIRING,
        compliance_alerts__status=ComplianceAlert.Status.OPEN,
    )

    created = 0
    already_open = 0
    for profile in expiring.only("id", "license_expiry_date"):
        try:
            with transaction.atomic():
                ComplianceAlert.objects.create(
                    profile=profile,
                    alert_type=ComplianceAlert.AlertType.LICENSE_EXPIRING,
                    due_date=profile.license_expiry_date,
                )
            created += 1
        except IntegrityError:
            # The unique constraint caught a concurrent run that got there
            # first. Its own savepoint, or this rollback would poison the rest
            # of the sweep.
            already_open += 1

    return {
        "lead_days": lead_days,
        "threshold": threshold.isoformat(),
        "alerts_created": created,
        "already_open": already_open,
    }
