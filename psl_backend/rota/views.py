from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_q.tasks import async_task
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsFacility, IsProfessional

from .models import Shift, ShiftSwapRequest
from .serializers import (
    PublishShiftsSerializer,
    ShiftSerializer,
    ShiftSwapRequestSerializer,
)


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


class ShiftSwapRequestCreateView(APIView):
    """A professional offers their own assigned shift for swap."""

    permission_classes = [IsProfessional]

    @extend_schema(
        request=ShiftSwapRequestSerializer,
        responses={201: ShiftSwapRequestSerializer},
        summary="Open a swap request on your shift",
    )
    def post(self, request, shift_id):
        profile = request.profile
        shift = get_object_or_404(Shift, pk=shift_id)

        # Only the assignee may offer the shift, which also scopes this to the
        # caller's own facility without a separate check.
        if shift.professional_id != profile.id:
            raise PermissionDenied("You are not assigned to that shift.")
        if not shift.is_published:
            return Response(
                {"detail": "Only published shifts can be swapped."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if shift.start_time <= timezone.now():
            return Response(
                {"detail": "That shift has already started."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ShiftSwapRequestSerializer(
            data=request.data,
            context={"facility": shift.facility, "requester": profile},
        )
        serializer.is_valid(raise_exception=True)

        try:
            # Its own atomic block so the constraint violation rolls back a
            # savepoint rather than poisoning the surrounding transaction —
            # otherwise every later query in the same request fails with
            # TransactionManagementError instead of returning this 409.
            with transaction.atomic():
                swap = ShiftSwapRequest.objects.create(
                    shift=shift,
                    requesting_professional=profile,
                    target_professional=serializer.validated_data.get(
                        "target_professional"
                    ),
                )
        except IntegrityError:
            # The partial unique constraint: one pending request per shift.
            # Without it, two open requests on the same shift could each be
            # accepted by a different person.
            return Response(
                {"detail": "There is already an open swap request for that shift."},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            ShiftSwapRequestSerializer(swap).data, status=status.HTTP_201_CREATED
        )


class SwapRequestListView(APIView):
    """Swap requests visible to this professional.

    Scoped to their own facility's roster: open requests they could accept,
    plus their own requests whatever the status.
    """

    permission_classes = [IsProfessional]

    @extend_schema(
        responses={200: ShiftSwapRequestSerializer(many=True)},
        summary="List swap requests",
    )
    def get(self, request):
        profile = request.profile
        queryset = (
            ShiftSwapRequest.objects.filter(shift__facility_id=profile.facility_id)
            .select_related(
                "shift",
                "requesting_professional",
                "target_professional",
                "accepted_by",
            )
            .order_by("-created_at")
        )
        requested_status = request.query_params.get("status")
        if requested_status:
            queryset = queryset.filter(status=requested_status)

        # A request aimed at one person is not the rest of the roster's business.
        queryset = queryset.filter(
            Q(target_professional__isnull=True)
            | Q(target_professional=profile)
            | Q(requesting_professional=profile)
        )
        return Response(ShiftSwapRequestSerializer(queryset, many=True).data)


class SwapRequestAcceptView(APIView):
    """Claim an open swap request.

    The claim is a single conditional UPDATE, never a read-then-write. Two
    professionals tapping accept at the same instant both run the same
    statement; the database lets exactly one match `status='pending'`, and the
    loser sees zero rows affected and gets a clean "already taken".
    """

    permission_classes = [IsProfessional]

    @extend_schema(
        responses={200: ShiftSwapRequestSerializer}, summary="Accept a swap"
    )
    def post(self, request, pk):
        profile = request.profile

        swap = get_object_or_404(
            ShiftSwapRequest.objects.select_related("shift"), pk=pk
        )
        if swap.shift.facility_id != profile.facility_id:
            raise NotFound("No such swap request.")
        if swap.requesting_professional_id == profile.id:
            return Response(
                {"detail": "You cannot accept your own swap request."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            swap.target_professional_id is not None
            and swap.target_professional_id != profile.id
        ):
            raise NotFound("No such swap request.")

        now = timezone.now()
        with transaction.atomic():
            claimed = ShiftSwapRequest.objects.filter(
                pk=pk, status=ShiftSwapRequest.Status.PENDING
            ).update(
                status=ShiftSwapRequest.Status.ACCEPTED,
                accepted_by=profile,
                decided_at=now,
            )
            if claimed != 1:
                # Someone else won the race, or it was cancelled meanwhile.
                return Response(
                    {"detail": "That swap request is no longer open."},
                    status=status.HTTP_409_CONFLICT,
                )

            shift = Shift.objects.select_for_update().get(pk=swap.shift_id)
            shift.professional = profile
            # save(), not update(): the reassignment is what a facility will
            # want to see in the audit trail.
            shift.save(update_fields=["professional"])

            swap.refresh_from_db()
            # Re-save so the claim reaches the swap's own history too. The
            # conditional UPDATE above cannot, and it is the one write that
            # must stay a single statement.
            swap.save()

        return Response(ShiftSwapRequestSerializer(swap).data)


class SwapRequestCancelView(APIView):
    """The requester withdraws their own open request."""

    permission_classes = [IsProfessional]

    @extend_schema(
        responses={200: ShiftSwapRequestSerializer}, summary="Cancel a swap"
    )
    def post(self, request, pk):
        profile = request.profile
        swap = get_object_or_404(
            ShiftSwapRequest.objects.select_related("shift"), pk=pk
        )
        if swap.shift.facility_id != profile.facility_id:
            raise NotFound("No such swap request.")
        if swap.requesting_professional_id != profile.id:
            raise PermissionDenied("Only the requester can cancel this.")

        cancelled = ShiftSwapRequest.objects.filter(
            pk=pk, status=ShiftSwapRequest.Status.PENDING
        ).update(
            status=ShiftSwapRequest.Status.CANCELLED, decided_at=timezone.now()
        )
        if cancelled != 1:
            return Response(
                {"detail": "That swap request is no longer open."},
                status=status.HTTP_409_CONFLICT,
            )
        swap.refresh_from_db()
        swap.save()
        return Response(ShiftSwapRequestSerializer(swap).data)
