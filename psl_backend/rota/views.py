from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsFacility

from .models import Shift
from .serializers import PublishShiftsSerializer, ShiftSerializer


class ShiftListCreateView(APIView):
    """List and create draft shifts for the calling facility."""

    permission_classes = [IsFacility]

    @extend_schema(
        responses={200: ShiftSerializer(many=True)}, summary="List shifts"
    )
    def get(self, request):
        shifts = (
            Shift.objects.filter(facility=request.facility)
            .select_related("professional")
            .order_by("start_time")
        )
        published = request.query_params.get("is_published")
        if published in ("true", "false"):
            shifts = shifts.filter(is_published=(published == "true"))
        return Response(ShiftSerializer(shifts, many=True).data)

    @extend_schema(
        request=ShiftSerializer,
        responses={201: ShiftSerializer},
        summary="Create a draft shift",
    )
    def post(self, request):
        serializer = ShiftSerializer(
            data=request.data, context={"facility": request.facility}
        )
        serializer.is_valid(raise_exception=True)
        shift = serializer.save()
        return Response(ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)


class PublishShiftsView(APIView):
    """Publish a set of draft shifts and notify the assigned professionals."""

    permission_classes = [IsFacility]

    @extend_schema(
        request=PublishShiftsSerializer,
        responses={200: None},
        summary="Publish shifts",
    )
    def post(self, request):
        serializer = PublishShiftsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shift_ids = serializer.validated_data["shift_ids"]

        # Scoped to the caller's own facility: ids from another facility are
        # simply not found rather than published across a tenant boundary.
        shifts = list(
            Shift.objects.filter(
                id__in=shift_ids, facility=request.facility, is_published=False
            )
        )

        now = timezone.now()
        published_ids = []
        to_notify = []
        with transaction.atomic():
            for shift in shifts:
                shift.is_published = True
                shift.published_at = now
                # Saved row by row rather than via queryset.update(), which
                # skips save() and would leave the publish out of the audit
                # trail entirely.
                shift.save(update_fields=["is_published", "published_at"])
                published_ids.append(shift.id)
                if shift.professional_id:
                    to_notify.append(
                        (shift.id, shift.professional_id, shift.role, shift.start_time)
                    )
        updated = len(published_ids)

        for shift_id, professional_id, role, start_time in to_notify:
            # Queued, not sent inline: 50 shifts must not mean 50 blocking
            # FCM calls inside this request.
            async_task(
                "rota.tasks.send_push_notification",
                professional_id=professional_id,
                title="New shift published",
                body=f"{role} — {timezone.localtime(start_time):%b %d, %I:%M %p}",
                data={"shift_id": shift_id, "kind": "shift_published"},
                task_name=f"shift-published-{shift_id}",
            )

        missing = sorted(set(shift_ids) - set(published_ids))
        return Response(
            {
                "published": updated,
                "notifications_queued": len(to_notify),
                "unassigned_shifts": updated - len(to_notify),
                "not_published": missing,
            }
        )
