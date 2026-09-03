from datetime import timedelta

from django.utils import timezone
from django_q.tasks import async_task
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsFacility
from profiles.models import Profile

from .models import Facility, InviteLink
from .serializers import (
    BulkImportUploadSerializer,
    FacilityRegistrationSerializer,
    FacilityStaffSerializer,
    InviteLinkSerializer,
)

DEFAULT_INVITE_VALID_DAYS = 14


class FacilityRegisterView(APIView):
    """Register a facility. Lands as `pending` for PSL staff to approve."""

    @extend_schema(
        request=FacilityRegistrationSerializer,
        responses={201: FacilityRegistrationSerializer},
        summary="Register a facility",
    )
    def post(self, request):
        supabase_user_id = request.user.supabase_user_id

        if Facility.objects.filter(supabase_user_id=supabase_user_id).exists():
            return Response(
                {"detail": "A facility is already registered for this account."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = FacilityRegistrationSerializer(
            data=request.data,
            context={"supabase_user_id": supabase_user_id, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        facility = serializer.save()
        return Response(
            FacilityRegistrationSerializer(facility).data,
            status=status.HTTP_201_CREATED,
        )


class FacilityBulkImportView(APIView):
    """Upload a staff CSV/Excel file. Parsing and account provisioning run in
    the background, so the response returns immediately regardless of size."""

    permission_classes = [IsFacility]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        request=BulkImportUploadSerializer,
        responses={202: None},
        summary="Bulk import staff from CSV/Excel",
    )
    def post(self, request):
        serializer = BulkImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded = serializer.validated_data["file"]

        # Read now: the uploaded file handle does not survive into the worker.
        content = uploaded.read()

        task_id = async_task(
            "facilities.tasks.run_bulk_import",
            facility_id=request.facility.id,
            content=content,
            filename=uploaded.name,
            task_name=f"bulk-import-{request.facility.id}",
        )

        return Response(
            {
                "detail": "Import started. Profiles will appear as rows are processed.",
                "task_id": str(task_id),
                "filename": uploaded.name,
                "bytes": len(content),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class BulkImportStatusView(APIView):
    """Read the outcome of a queued import.

    The upload endpoint answers 202 with a task id because the work runs in a
    worker, so the per-row report only exists once django-q2 finishes. Without
    this the facility has no way to learn which rows failed.
    """

    permission_classes = [IsFacility]

    @extend_schema(responses={200: None}, summary="Bulk import status")
    def get(self, request, task_id):
        from django_q.models import Task

        task = Task.objects.filter(id=task_id).first()
        if task is None:
            # django-q2 only writes a Task row on completion, so an absent row
            # means queued or still running. An unknown id looks the same; the
            # caller times out rather than being told whether it exists.
            return Response({"status": "pending"})

        # Task ids are unguessable, but scope anyway: never let one facility
        # read another's import report.
        if task.name != f"bulk-import-{request.facility.id}":
            raise NotFound("No such import for this facility.")

        return Response(
            {
                "status": "success" if task.success else "failed",
                "result": task.result,
                "started": task.started,
                "stopped": task.stopped,
            }
        )


class FacilityStaffView(APIView):
    """List this facility's professionals, for the rota assignment dropdown."""

    permission_classes = [IsFacility]

    @extend_schema(
        responses={200: FacilityStaffSerializer(many=True)},
        summary="List facility staff",
    )
    def get(self, request):
        staff = Profile.objects.filter(facility=request.facility).order_by("full_name")
        return Response(FacilityStaffSerializer(staff, many=True).data)


class InviteLinkCreateView(APIView):
    """Generate an invite link for the calling facility."""

    permission_classes = [IsFacility]

    @extend_schema(
        request=InviteLinkSerializer,
        responses={201: InviteLinkSerializer},
        summary="Create an invite link",
    )
    def post(self, request):
        serializer = InviteLinkSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        expires_at = serializer.validated_data.get("expires_at") or (
            timezone.now() + timedelta(days=DEFAULT_INVITE_VALID_DAYS)
        )
        invite = InviteLink.objects.create(
            facility=request.facility, expires_at=expires_at
        )
        return Response(
            InviteLinkSerializer(invite, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
