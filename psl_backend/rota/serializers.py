from rest_framework import serializers

from profiles.models import Profile

from .models import Shift


class ShiftSerializer(serializers.ModelSerializer):
    professional_name = serializers.CharField(
        source="professional.full_name", read_only=True, default=None
    )

    class Meta:
        model = Shift
        fields = (
            "id",
            "professional",
            "professional_name",
            "role",
            "start_time",
            "end_time",
            "is_published",
            "published_at",
            "reminder_sent",
            "created_at",
        )
        # Publishing is its own endpoint, so it cannot be set on create.
        read_only_fields = (
            "id",
            "is_published",
            "published_at",
            "reminder_sent",
            "created_at",
        )

    def validate(self, attrs):
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start and end and end <= start:
            raise serializers.ValidationError(
                {"end_time": "end_time must be after start_time."}
            )
        return attrs

    def validate_professional(self, value):
        """A facility may only roster its own staff.

        Without this check, a facility could assign shifts to any professional
        in the system by guessing an id, and would then receive their details
        back in the response.
        """
        if value is None:
            return value
        facility = self.context["facility"]
        if value.facility_id != facility.id:
            raise serializers.ValidationError(
                "That professional is not on this facility's roster."
            )
        return value

    def create(self, validated_data):
        return Shift.objects.create(
            facility=self.context["facility"], **validated_data
        )


class PublishShiftsSerializer(serializers.Serializer):
    shift_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False, max_length=500
    )
