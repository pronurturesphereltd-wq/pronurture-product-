"""Register (or refresh) the daily compliance sweep.

Idempotent, safe on every deploy — the same shape as setup_shift_reminders.

`next_run` is written explicitly rather than left to django-q2, because a
Schedule stores an absolute timestamp and does not self-heal. A clock that was
running fast when the schedule was created leaves the sweep stalled for the
size of the correction, silently, with nothing logged. That has already
happened once on this project. Re-run this command after any clock change.

Setting `next_run` to now means the sweep fires on the next `qcluster` tick and
daily thereafter. Re-running on deploy re-fires it — harmless, since raising an
alert that already exists is a no-op.
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django_q.models import Schedule

SCHEDULE_NAME = "compliance-checks"
TASK_PATH = "compliance.tasks.check_compliance"


class Command(BaseCommand):
    help = "Create or update the daily compliance check schedule."

    def add_arguments(self, parser):
        parser.add_argument(
            "--run-now",
            action="store_true",
            help="Also run the sweep inline once, without waiting for qcluster.",
        )

    def handle(self, *args, **options):
        schedule, created = Schedule.objects.update_or_create(
            name=SCHEDULE_NAME,
            defaults={
                "func": TASK_PATH,
                "schedule_type": Schedule.DAILY,
                "repeats": -1,
                "next_run": timezone.now(),
            },
        )
        verb = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} schedule '{schedule.name}': {TASK_PATH} daily. "
                f"Alerts are raised for licences expiring within "
                f"{settings.COMPLIANCE_LICENSE_LEAD_DAYS} days."
            )
        )
        self.stdout.write(
            f"next_run set to {schedule.next_run:%Y-%m-%d %H:%M:%S %Z}. "
            "The schedule only runs while `manage.py qcluster` is running."
        )

        if options["run_now"]:
            from compliance.tasks import check_compliance

            result = check_compliance()
            self.stdout.write(self.style.SUCCESS(f"Ran once inline: {result}"))
