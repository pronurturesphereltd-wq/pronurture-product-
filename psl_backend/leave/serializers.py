from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import LeaveApplication


class LeaveApplicationSerializer(serializers.ModelSerializer):
    professional_name = serializers.CharField(
        source="professional.full_name", read_only=True
    )
    professional_email = serializers.EmailField(
        source="professional.email", read_only=True
    )
    days = serializers.SerializerMethodField()

    class Meta:
        model = LeaveApplication
        fields = (
            "id",
            "professional",
            "professional_name",
            "professional_email",
            "start_date",
            "end_date",
            "days",
            "reason",
            "status",
            "created_at",
            "decided_at",
        )
        # The applicant is the authenticated caller, never a field in the body:
        # otherwise anyone could file leave in a colleague's name. Status moves
        # only through the approve/decline endpoints.
        read_only_fields = (
            "id",
            "professional",
            "status",
            "created_at",
            "decided_at",
        )

    @extend_schema_field(serializers.IntegerField())
    def get_days(self, obj):
        """Inclusive day count, so a single-day leave reads as 1, not 0."""
        return (obj.end_date - obj.start_date).days + 1

    def validate(self, attrs):
        start = attrs.get("start_date")
        end = attrs.get("end_date")
        if start and end and end < start:
            raise serializers.ValidationError(
                {"end_date": "end_date cannot be before start_date."}
            )
        return attrs
