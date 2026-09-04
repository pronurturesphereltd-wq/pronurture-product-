from django.db import models


class ComplianceAlert(models.Model):
    class AlertType(models.TextChoices):
        LICENSE_EXPIRING = "license_expiring", "License expiring"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    profile = models.ForeignKey(
        "profiles.Profile", on_delete=models.CASCADE, related_name="compliance_alerts"
    )
    alert_type = models.CharField(max_length=30, choices=AlertType.choices)
    due_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "-created_at"]
        constraints = [
            # The sweep's idempotency guard, enforced by the database rather
            # than only by its .exclude() clause: two runs racing each other
            # cannot both create an open alert for the same profile and type.
            models.UniqueConstraint(
                fields=["profile", "alert_type"],
                condition=models.Q(status="open"),
                name="one_open_alert_per_profile_and_type",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "due_date"], name="alert_status_due_idx"),
        ]

    def __str__(self):
        return f"{self.get_alert_type_display()} for {self.profile.full_name}"
