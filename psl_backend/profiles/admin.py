from django.contrib import admin, messages
from django.utils import timezone
from simple_history.admin import SimpleHistoryAdmin

from .models import Profile, PushDevice


@admin.register(Profile)
class ProfileAdmin(SimpleHistoryAdmin):
    list_display = (
        "full_name",
        "email",
        "verification_state",
        "onboarding_path",
        # Drives the compliance sweep. Set here during licence verification —
        # it is PSL's data, not something a facility uploads.
        "license_expiry_date",
        "created_at",
    )
    list_filter = ("verification_state", "onboarding_path")
    search_fields = ("full_name", "email", "license_number")
    readonly_fields = ("verified_at", "verified_by", "created_at", "updated_at")
    actions = ("verify_profiles", "reject_profiles")

    @admin.action(description="Verify selected profiles")
    def verify_profiles(self, request, queryset):
        count = self._set_state(request, queryset, Profile.VerificationState.VERIFIED)
        self.message_user(request, f"{count} profile(s) verified.", messages.SUCCESS)

    @admin.action(description="Reject selected profiles")
    def reject_profiles(self, request, queryset):
        count = self._set_state(request, queryset, Profile.VerificationState.REJECTED)
        self.message_user(request, f"{count} profile(s) rejected.", messages.SUCCESS)

    def _set_state(self, request, queryset, state):
        """Save row by row so django-simple-history records each transition."""
        now = timezone.now()
        count = 0
        for profile in queryset:
            profile.verification_state = state
            profile.verified_at = now
            profile.verified_by = request.user
            profile.save()
            count += 1
        return count


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ("profile", "device_type", "created_at", "updated_at")
    list_filter = ("device_type",)
    search_fields = ("profile__full_name", "profile__email")
    readonly_fields = ("created_at", "updated_at")
