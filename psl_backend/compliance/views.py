"""Facility-facing compliance endpoints.

Routed under `/api/facilities/` because that is whose dashboard they belong to,
while the model and its sweep live here with the rest of compliance.
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsFacility

from .models import ComplianceAlert
from .serializers import ComplianceAlertSerializer


class ComplianceAlertListView(APIView):
    """Open compliance alerts for this facility's roster."""

    permission_classes = [IsFacility]

    @extend_schema(
        responses={200: ComplianceAlertSerializer(many=True)},
        summary="List compliance alerts",
    )
    def get(self, request):
        queryset = ComplianceAlert.objects.filter(
            profile__facility=request.facility
        ).select_related("profile")

        # Open by default: a dashboard of things needing action, not an
        # archive. `?status=all` opts into the full history.
        requested_status = request.query_params.get("status", "open")
        if requested_status != "all":
            queryset = queryset.filter(status=requested_status)

        return Response(ComplianceAlertSerializer(queryset, many=True).data)


class ComplianceAlertResolveView(APIView):
    """Mark an alert handled.

    Note what closing an alert does not do: if the licence is still expiring,
    the next daily sweep raises a fresh one, because the profile still matches.
    That is deliberate rather than a loop bug — the alert stops recurring when
    the underlying `license_expiry_date` is renewed, which PSL staff do during
    licence verification. Resolving is for alerts handled some other way.
    """

    permission_classes = [IsFacility]

    @extend_schema(
        request=None,
        responses={200: ComplianceAlertSerializer},
        summary="Resolve an alert",
    )
    def post(self, request, pk):
        alert = get_object_or_404(
            ComplianceAlert.objects.select_related("profile"), pk=pk
        )
        if alert.profile.facility_id != request.facility.id:
            raise NotFound("No such compliance alert.")

        resolved = ComplianceAlert.objects.filter(
            pk=pk, status=ComplianceAlert.Status.OPEN
        ).update(status=ComplianceAlert.Status.RESOLVED, resolved_at=timezone.now())
        if resolved != 1:
            return Response(
                {"detail": "That alert is already resolved."},
                status=status.HTTP_409_CONFLICT,
            )

        alert.refresh_from_db()
        return Response(ComplianceAlertSerializer(alert).data)
