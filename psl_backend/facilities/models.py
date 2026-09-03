from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from core.history import get_history_user


class Facility(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=100)
    contact_email = models.EmailField(unique=True)
    supabase_user_id = models.UUIDField(
        unique=True,
        null=True,
        blank=True,
        help_text="Supabase Auth user id captured from the verified JWT at registration.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_facilities",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords(get_user=get_history_user)

    class Meta:
        verbose_name_plural = "facilities"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
