from rest_framework import serializers

from .models import Facility


class FacilityRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = (
            "id",
            "name",
            "registration_number",
            "contact_email",
            "status",
            "created_at",
        )
        # Approval is an admin decision, never something a registrant can set.
        read_only_fields = ("id", "status", "created_at")

    def create(self, validated_data):
        return Facility.objects.create(
            status=Facility.Status.PENDING,
            supabase_user_id=self.context["supabase_user_id"],
            **validated_data,
        )
