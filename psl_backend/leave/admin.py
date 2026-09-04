from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import LeaveApplication


@admin.register(LeaveApplication)
class LeaveApplicationAdmin(SimpleHistoryAdmin):
    """Visibility for PSL staff. Approving is the facility's call, through the
    web app — a facility cannot log in here at all, since Django Admin uses
    Django's own staff auth rather than Supabase identities."""

    list_display = (
        "professional",
        "facility",
        "start_date",
        "end_date",
        "status",
        "created_at",
    )
    list_filter = ("status", "professional__facility")
    search_fields = ("professional__full_name", "professional__email", "reason")
    readonly_fields = ("decided_at", "created_at")
    date_hierarchy = "start_date"

    @admin.display(description="Facility", ordering="professional__facility__name")
    def facility(self, obj):
        return obj.professional.facility

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "professional", "professional__facility"
        )
