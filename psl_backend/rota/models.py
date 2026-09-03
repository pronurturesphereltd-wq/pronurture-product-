from django.db import models
from simple_history.models import HistoricalRecords

from core.history import get_history_user


class Shift(models.Model):
    facility = models.ForeignKey("facilities.Facility", on_delete=models.CASCADE)
    professional = models.ForeignKey(
        "profiles.Profile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Nullable: a draft shift can exist before anyone is assigned.",
    )
    role = models.CharField(max_length=100)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    # Idempotency guard for the scheduled reminder sweep. Without it, a shift
    # sitting inside the lookahead window would be reminded again on every run.
    reminder_sent = models.BooleanField(default=False)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords(get_user=get_history_user)

    class Meta:
        ordering = ["start_time"]
        indexes = [
            # The reminder sweep filters on exactly this combination.
            models.Index(
                fields=["is_published", "reminder_sent", "start_time"],
                name="shift_reminder_sweep_idx",
            ),
            models.Index(fields=["facility", "start_time"], name="shift_facility_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="shift_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return f"{self.role} @ {self.start_time:%Y-%m-%d %H:%M}"
