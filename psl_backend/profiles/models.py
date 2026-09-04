from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from core.history import get_history_user


class Profile(models.Model):
    class VerificationState(models.TextChoices):
        PENDING = "pending", "Pending"
        SELF_REGISTERED_UNVERIFIED = (
            "self_registered_unverified",
            "Self-registered (unverified)",
        )
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    class OnboardingPath(models.TextChoices):
        BULK_IMPORT = "bulk_import", "Bulk import"
        INVITE_LINK = "invite_link", "Invite link"

    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    license_number = models.CharField(max_length=100)
    license_body = models.CharField(max_length=255)
    license_expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Drives the compliance sweep. Null means never checked.",
    )
    supabase_user_id = models.UUIDField(
        unique=True,
        null=True,
        blank=True,
        help_text="Supabase Auth user id captured from the verified JWT at registration.",
    )
    facility = models.ForeignKey(
        "facilities.Facility",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profiles",
    )
    verification_state = models.CharField(
        max_length=32,
        choices=VerificationState.choices,
        default=VerificationState.PENDING,
    )
    onboarding_path = models.CharField(
        max_length=20,
        choices=OnboardingPath.choices,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords(get_user=get_history_user)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.email})"


class PushDevice(models.Model):
    class DeviceType(models.TextChoices):
        IOS = "ios", "iOS"
        ANDROID = "android", "Android"

    profile = models.ForeignKey(
        "profiles.Profile", on_delete=models.CASCADE, related_name="push_devices"
    )
    fcm_token = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=20, choices=DeviceType.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_device_type_display()} device for {self.profile.full_name}"
