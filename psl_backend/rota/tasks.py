"""Background jobs for the rota.

`send_push_notification` is queued rather than called inline so that publishing
50 shifts does not block the HTTP response on 50 outbound FCM calls.

`send_shift_reminders` is the scheduled sweep, registered to run periodically.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from core.push import send_to_tokens
from profiles.models import Profile, PushDevice
from rota.models import Shift

logger = logging.getLogger(__name__)


def send_push_notification(professional_id, title, body, data=None):
    """Push to every device registered to one professional."""
    try:
        profile = Profile.objects.get(pk=professional_id)
    except Profile.DoesNotExist:
        logger.warning("Push skipped: profile %s no longer exists.", professional_id)
        return {"sent": 0, "failed": 0, "reason": "profile-missing"}

    tokens = list(profile.push_devices.values_list("fcm_token", flat=True))
    if not tokens:
        return {"sent": 0, "failed": 0, "reason": "no-devices"}

    sent, failed, invalid = send_to_tokens(tokens, title, body, data)

    if invalid:
        # A token FCM calls unregistered never recovers. Dropping it keeps the
        # next publish from retrying a dead handset forever.
        PushDevice.objects.filter(fcm_token__in=invalid).delete()

    return {"sent": sent, "failed": failed, "pruned_devices": len(invalid)}


def send_shift_reminders():
    """Remind professionals about shifts starting soon. Runs on a schedule.

    Idempotency is the whole game here: this runs every few minutes over an
    overlapping window, so the `reminder_sent` flag is claimed in a single
    atomic UPDATE before any push is queued. Two overlapping runs cannot both
    claim the same shift, so nobody is reminded twice.
    """
    now = timezone.now()
    lead = settings.SHIFT_REMINDER_LEAD_MINUTES
    spread = settings.SHIFT_REMINDER_WINDOW_MINUTES
    window_start = now + timedelta(minutes=lead - spread)
    window_end = now + timedelta(minutes=lead + spread)

    candidates = list(
        Shift.objects.filter(
            is_published=True,
            reminder_sent=False,
            professional__isnull=False,
            start_time__gte=window_start,
            start_time__lte=window_end,
        ).values_list("id", "professional_id", "role", "start_time")
    )

    claimed = []
    for shift_id, professional_id, role, start_time in candidates:
        # Claim atomically. The reminder_sent=False in the filter means a
        # concurrent run that got there first updates 0 rows and we skip it.
        with transaction.atomic():
            updated = Shift.objects.filter(id=shift_id, reminder_sent=False).update(
                reminder_sent=True, reminder_sent_at=timezone.now()
            )
        if updated:
            claimed.append((shift_id, professional_id, role, start_time))

    for shift_id, professional_id, role, start_time in claimed:
        _queue_reminder(shift_id, professional_id, role, start_time)

    return {
        "checked": len(candidates),
        "reminded": len(claimed),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


def _queue_reminder(shift_id, professional_id, role, start_time):
    async_task(
        "rota.tasks.send_push_notification",
        professional_id=professional_id,
        title="Shift starting soon",
        body=f"{role} starts at {timezone.localtime(start_time):%I:%M %p}",
        data={"shift_id": shift_id, "kind": "shift_reminder"},
        task_name=f"shift-reminder-{shift_id}",
    )
