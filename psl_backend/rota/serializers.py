from rest_framework import serializers

from profiles.models import Profile

from .models import Shift, ShiftSwapRequest
from .roles import role_matches


class EligibleColleagueSerializer(serializers.Serializer):
    """Someone a shift may be offered to.

    Read by a colleague rather than by the facility, so it is narrower than
    FacilityStaffSerializer: enough to pick a person from a list, and nothing
    about their licence or contact details beyond the address the roster
    already shows.
    """

    id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    role = serializers.CharField(read_only=True)
    verification_state = serializers.CharField(read_only=True)


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
            "ward",
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


class ShiftSwapRequestSerializer(serializers.ModelSerializer):
    shift_role = serializers.CharField(source="shift.role", read_only=True)
    shift_ward = serializers.CharField(source="shift.ward", read_only=True)
    shift_start_time = serializers.DateTimeField(
        source="shift.start_time", read_only=True
    )
    requesting_professional_name = serializers.CharField(
        source="requesting_professional.full_name", read_only=True
    )
    target_professional_name = serializers.CharField(
        source="target_professional.full_name", read_only=True, default=None
    )
    accepted_by_name = serializers.CharField(
        source="accepted_by.full_name", read_only=True, default=None
    )

    class Meta:
        model = ShiftSwapRequest
        fields = (
            "id",
            "shift",
            "shift_role",
            "shift_ward",
            "shift_start_time",
            "requesting_professional",
            "requesting_professional_name",
            "target_professional",
            "target_professional_name",
            "accepted_by",
            "accepted_by_name",
            "status",
            "created_at",
            "decided_at",
        )
        read_only_fields = (
            "id",
            "shift",
            "requesting_professional",
            "accepted_by",
            "status",
            "created_at",
            "decided_at",
        )

    def validate_target_professional(self, value):
        """Offering a shift to someone on another facility's roster would leak
        the shift across a tenant boundary."""
        if value is None:
            return value
        facility = self.context["facility"]
        if value.facility_id != facility.id:
            raise serializers.ValidationError(
                "That professional is not on this facility's roster."
            )
        if value.id == self.context["requester"].id:
            raise serializers.ValidationError(
                "You cannot offer a shift to yourself."
            )
        # Re-checked here even though the eligible-colleagues endpoint only
        # offers matching roles. The API is the boundary, not the UI: nothing
        # stops a caller naming any id, and the guardrail exists to keep a
        # general nurse off an ENT Registrar's shift.
        shift = self.context["shift"]
        if not role_matches(value.role, shift.role):
            raise serializers.ValidationError(
                f"{value.full_name} is not designated '{shift.role}'"
                + (f" (they are '{value.role}')." if value.role else " (no role set).")
            )
        return value

    def validate(self, attrs):
        """Every offer names someone. Open offers are gone: a shift left for
        whoever grabs it first is exactly what this rule removes."""
        if not attrs.get("target_professional"):
            raise serializers.ValidationError(
                {
                    "target_professional": (
                        "You must choose a colleague to offer this shift to."
                    )
                }
            )
        return attrs
