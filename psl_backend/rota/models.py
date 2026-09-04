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
    role = models.CharField(
        max_length=100,
        help_text=(
            "What the shift needs covering, e.g. 'ENT Registrar'. Compared "
            "against Profile.role when someone accepts a swap."
        ),
    )
    ward = models.CharField(
        max_length=100,
        blank=True,
        help_text="Informational only. Never gates anything, by design.",
    )
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


class ShiftSwapRequest(models.Model):
    """A professional offering their assigned shift to someone else.

    Peer-to-peer: management sees the outcome but is not an approval gate.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    shift = models.ForeignKey(
        "rota.Shift", on_delete=models.CASCADE, related_name="swap_requests"
    )
    requesting_professional = models.ForeignKey(
        "profiles.Profile",
        related_name="swap_requests_made",
        on_delete=models.CASCADE,
    )
    target_professional = models.ForeignKey(
        "profiles.Profile",
        null=True,
        blank=True,
        related_name="swap_requests_targeted",
        on_delete=models.SET_NULL,
        help_text="Null means open to anyone on the facility's roster.",
    )
    accepted_by = models.ForeignKey(
        "profiles.Profile",
        null=True,
        blank=True,
        related_name="swap_requests_accepted",
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords(get_user=get_history_user)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A shift can only be offered once at a time. Without this, a
            # professional could open several pending requests on one shift and
            # two different people could each "win" a different request.
            models.UniqueConstraint(
                fields=["shift"],
                condition=models.Q(status="pending"),
                name="one_pending_swap_per_shift",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="swap_status_idx"),
        ]

    def __str__(self):
        return f"Swap for {self.shift} ({self.status})"
