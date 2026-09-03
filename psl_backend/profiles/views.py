from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsProfessional
from facilities.models import InviteLink

from .models import Profile, PushDevice
from .serializers import (
    ProfileInviteRegisterSerializer,
    ProfileSeedSerializer,
    ProfileSelfRegisterSerializer,
    PushDeviceSerializer,
)


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


class ProfileRegisterViaInviteView(APIView):
    """Self-register through a facility's invite link.

    Public: the unguessable token in the URL is the authorisation. A Supabase
    JWT is optional — if one is supplied it links the new Profile to that auth
    account, but a professional can register before having an account.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=ProfileInviteRegisterSerializer,
        responses={201: ProfileInviteRegisterSerializer},
        summary="Register via an invite link",
    )
    def post(self, request, token):
        invite = get_object_or_404(InviteLink, token=token)
        if invite.is_expired:
            return Response(
                {"detail": "This invite link has expired."},
                status=status.HTTP_410_GONE,
            )

        serializer = ProfileInviteRegisterSerializer(
            data=request.data,
            context={"facility": invite.facility, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        return Response(
            ProfileInviteRegisterSerializer(profile).data,
            status=status.HTTP_201_CREATED,
        )


class PushDeviceRegisterView(APIView):
    """Register or update this professional's FCM token."""

    permission_classes = [IsProfessional]

    @extend_schema(
        request=PushDeviceSerializer,
        responses={200: PushDeviceSerializer, 201: PushDeviceSerializer},
        summary="Register a device for push notifications",
    )
    def post(self, request):
        serializer = PushDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fcm_token = serializer.validated_data["fcm_token"]

        # A handset re-registering, or one that changed hands between staff,
        # must move to the current profile rather than collide on the unique
        # token or keep pushing shifts to the previous owner.
        device, created = PushDevice.objects.update_or_create(
            fcm_token=fcm_token,
            defaults={
                "profile": request.profile,
                "device_type": serializer.validated_data["device_type"],
            },
        )
        return Response(
            PushDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
