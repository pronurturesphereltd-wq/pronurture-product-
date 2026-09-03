"""Register (or refresh) the periodic shift-reminder schedule.

Idempotent: safe to run on every deploy. Kept as a management command rather
than a data migration because django-q2's Schedule table is operational state,
not schema, and the interval is environment-tunable.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django_q.models import Schedule

SCHEDULE_NAME = "shift-reminders"
TASK_PATH = "rota.tasks.send_shift_reminders"


class Command(BaseCommand):
    help = "Create or update the periodic shift reminder schedule."

    def handle(self, *args, **options):
        minutes = settings.SHIFT_REMINDER_INTERVAL_MINUTES
        schedule, created = Schedule.objects.update_or_create(
            name=SCHEDULE_NAME,
            defaults={
                "func": TASK_PATH,
                "schedule_type": Schedule.MINUTES,
                "minutes": minutes,
                # repeats=-1 means "forever"; anything else eventually stops.
                "repeats": -1,
                "next_run": timezone.now(),
            },
        )
        verb = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} schedule '{schedule.name}': {TASK_PATH} every {minutes} min. "
                f"Reminders fire for shifts starting in ~"
                f"{settings.SHIFT_REMINDER_LEAD_MINUTES} min "
                f"(+/- {settings.SHIFT_REMINDER_WINDOW_MINUTES} min window)."
            )
        )
        self.stdout.write(
            "This only registers the schedule. It runs when `manage.py qcluster` "
            "is running."
        )
