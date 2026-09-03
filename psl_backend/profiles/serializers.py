from rest_framework import serializers

from .models import Profile

WRITABLE_FIELDS = (
    "full_name",
    "email",
    "phone",
    "license_number",
    "license_body",
)

READ_ONLY_FIELDS = (
    "id",
    "verification_state",
    "onboarding_path",
    "created_at",
)


class ProfileSeedListSerializer(serializers.ListSerializer):
    """Catches collisions *within* one import.

    Per-row unique validation only checks against rows already in the database,
    so two identical emails in the same payload both pass and then collide on
    insert. That surfaces as a 500 rather than a 400 unless caught here.
    """

    def validate(self, attrs):
        seen = set()
        duplicates = set()
        for row in attrs:
            email = (row.get("email") or "").lower()
            if not email:
                continue
            if email in seen:
                duplicates.add(email)
            seen.add(email)
        if duplicates:
            raise serializers.ValidationError(
                "Duplicate email addresses in this import: "
                + ", ".join(sorted(duplicates))
            )
        return attrs


class ProfileSeedSerializer(serializers.ModelSerializer):
    """One row of the bulk import. Many of these arrive in a JSON array."""

    class Meta:
        model = Profile
        fields = (*READ_ONLY_FIELDS, *WRITABLE_FIELDS, "facility")
        read_only_fields = READ_ONLY_FIELDS
        list_serializer_class = ProfileSeedListSerializer

    def create(self, validated_data):
        return Profile.objects.create(
            onboarding_path=Profile.OnboardingPath.BULK_IMPORT,
            verification_state=Profile.VerificationState.PENDING,
            **validated_data,
        )


class ProfileSelfRegisterSerializer(serializers.ModelSerializer):
    """A professional registering themselves via an invite link.

    Their licence is unverified until PSL staff check it, which is why this
    lands in `self_registered_unverified` rather than plain `pending`.
    """

    class Meta:
        model = Profile
        fields = (*READ_ONLY_FIELDS, *WRITABLE_FIELDS, "facility")
        read_only_fields = READ_ONLY_FIELDS

    def create(self, validated_data):
        return Profile.objects.create(
            onboarding_path=Profile.OnboardingPath.INVITE_LINK,
            verification_state=Profile.VerificationState.SELF_REGISTERED_UNVERIFIED,
            supabase_user_id=self.context["supabase_user_id"],
            **validated_data,
        )
