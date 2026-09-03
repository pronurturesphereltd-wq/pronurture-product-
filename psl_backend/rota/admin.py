from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Shift


@admin.register(Shift)
class ShiftAdmin(SimpleHistoryAdmin):
    list_display = (
        "role",
        "facility",
        "professional",
        "start_time",
        "is_published",
        "reminder_sent",
    )
    list_filter = ("is_published", "reminder_sent", "facility")
    search_fields = ("role", "professional__full_name", "facility__name")
    readonly_fields = ("published_at", "reminder_sent_at", "created_at")
    date_hierarchy = "start_time"
