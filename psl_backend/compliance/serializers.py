from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import ComplianceAlert


class ComplianceAlertSerializer(serializers.ModelSerializer):
    professional_name = serializers.CharField(
        source="profile.full_name", read_only=True
    )
    professional_email = serializers.EmailField(source="profile.email", read_only=True)
    license_number = serializers.CharField(source="profile.license_number", read_only=True)
    license_body = serializers.CharField(source="profile.license_body", read_only=True)
    alert_type_display = serializers.CharField(
        source="get_alert_type_display", read_only=True
    )
    days_until_due = serializers.SerializerMethodField()

    class Meta:
        model = ComplianceAlert
        fields = (
            "id",
            "profile",
            "professional_name",
            "professional_email",
            "license_number",
            "license_body",
            "alert_type",
            "alert_type_display",
            "due_date",
            "days_until_due",
            "status",
            "created_at",
            "resolved_at",
        )
        # Every field is written by the sweep or the resolve endpoint. Nothing
        # here is client-supplied.
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField())
    def get_days_until_due(self, obj):
        """Negative once the licence has actually expired, which is the case
        the facility most needs to see at a glance."""
        return (obj.due_date - timezone.now().date()).days
