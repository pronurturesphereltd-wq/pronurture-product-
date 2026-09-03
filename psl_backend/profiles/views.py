from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Profile
from .serializers import ProfileSeedSerializer, ProfileSelfRegisterSerializer


class ProfileSeedBulkView(APIView):
    """Stub bulk import. Accepts a JSON array of profiles."""

    @extend_schema(
        request=ProfileSeedSerializer(many=True),
        responses={201: ProfileSeedSerializer(many=True)},
        summary="Bulk import profiles (stub)",
    )
    def post(self, request):
        if not isinstance(request.data, list):
            return Response(
                {"detail": "Expected a JSON array of profile objects."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ProfileSeedSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        # All or nothing: a partially applied import would leave staff unsure
        # which rows actually landed.
        with transaction.atomic():
            profiles = serializer.save()

        return Response(
            ProfileSeedSerializer(profiles, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class ProfileSelfRegisterView(APIView):
    """A professional registers themselves against their Supabase identity."""

    @extend_schema(
        request=ProfileSelfRegisterSerializer,
        responses={201: ProfileSelfRegisterSerializer},
        summary="Self-register a professional",
    )
    def post(self, request):
        supabase_user_id = request.user.supabase_user_id

        if Profile.objects.filter(supabase_user_id=supabase_user_id).exists():
            return Response(
                {"detail": "A profile is already registered for this account."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ProfileSelfRegisterSerializer(
            data=request.data,
            context={"supabase_user_id": supabase_user_id, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        return Response(
            ProfileSelfRegisterSerializer(profile).data,
            status=status.HTTP_201_CREATED,
        )
