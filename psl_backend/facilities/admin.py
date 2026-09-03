from django.contrib import admin, messages
from django.utils import timezone
from simple_history.admin import SimpleHistoryAdmin

from .models import Facility


@admin.register(Facility)
class FacilityAdmin(SimpleHistoryAdmin):
    list_display = ("name", "contact_email", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "contact_email", "registration_number")
    readonly_fields = ("approved_at", "approved_by", "created_at")
    actions = ("approve_facilities", "reject_facilities")

    @admin.action(description="Approve selected facilities")
    def approve_facilities(self, request, queryset):
        count = self._set_status(request, queryset, Facility.Status.APPROVED)
        self.message_user(request, f"{count} facility(ies) approved.", messages.SUCCESS)

    @admin.action(description="Reject selected facilities")
    def reject_facilities(self, request, queryset):
        count = self._set_status(request, queryset, Facility.Status.REJECTED)
        self.message_user(request, f"{count} facility(ies) rejected.", messages.SUCCESS)

    def _set_status(self, request, queryset, status):
        """Save row by row so django-simple-history records each transition."""
        now = timezone.now()
        count = 0
        for facility in queryset:
            facility.status = status
            facility.approved_at = now
            facility.approved_by = request.user
            facility.save()
            count += 1
        return count
