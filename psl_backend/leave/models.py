from django.db import models
from simple_history.models import HistoricalRecords

from core.history import get_history_user


class LeaveApplication(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"

    professional = models.ForeignKey(
        "profiles.Profile", on_delete=models.CASCADE, related_name="leave_applications"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords(get_user=get_history_user)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="leave_ends_on_or_after_it_starts",
            ),
        ]

    def __str__(self):
        return f"{self.professional.full_name}: {self.start_date} to {self.end_date}"
