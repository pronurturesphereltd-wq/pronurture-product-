from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .importing import SUPPORTED_EXTENSIONS, extension_of
from .models import Facility, InviteLink


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


class BulkImportUploadSerializer(serializers.Serializer):
    """Validates the upload itself. Parsing happens in the background task."""

    file = serializers.FileField()

    def validate_file(self, uploaded):
        extension = extension_of(uploaded.name)
        if extension not in SUPPORTED_EXTENSIONS:
            raise serializers.ValidationError(
                f"Unsupported file type '{extension or uploaded.name}'. "
                f"Use one of: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
            )
        if uploaded.size > settings.BULK_IMPORT_MAX_BYTES:
            limit_mb = settings.BULK_IMPORT_MAX_BYTES / (1024 * 1024)
            raise serializers.ValidationError(
                f"File is too large ({uploaded.size} bytes). Limit is {limit_mb:.0f} MB."
            )
        if uploaded.size == 0:
            raise serializers.ValidationError("File is empty.")
        return uploaded


class FacilityStaffSerializer(serializers.Serializer):
    """A facility's own professionals, for the rota's assignment dropdown.

    Read-only and deliberately thin: no licence numbers or phone numbers, since
    the rota screen only needs to name someone.
    """

    id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    verification_state = serializers.CharField(read_only=True)
    onboarding_path = serializers.CharField(read_only=True)


class InviteLinkSerializer(serializers.ModelSerializer):
    register_url = serializers.SerializerMethodField()

    class Meta:
        model = InviteLink
        fields = ("id", "token", "expires_at", "created_at", "register_url")
        read_only_fields = ("id", "token", "created_at")
        # Omitted, the view falls back to a default validity window.
        extra_kwargs = {"expires_at": {"required": False}}

    def get_register_url(self, obj) -> str:
        request = self.context.get("request")
        path = f"/api/profiles/register-via-invite/{obj.token}/"
        return request.build_absolute_uri(path) if request else path

    def validate_expires_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("expires_at must be in the future.")
        return value
