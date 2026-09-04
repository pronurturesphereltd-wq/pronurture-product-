from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_q.tasks import async_task
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsFacility, IsFacilityOrProfessional, IsProfessional

from .models import Shift, ShiftSwapRequest
from .roles import role_matches
from .serializers import (
    PublishShiftsSerializer,
    ShiftSerializer,
    ShiftSwapRequestSerializer,
)


def _role_mismatch_message(profile_role, shift_role):
    """A blank role is the common case at first — the field is new and nothing
    backfills it — so it gets an answer that says what to do about it, rather
    than reporting that the caller is designated ''."""
    if not profile_role.strip():
        return (
            "Your profile has no designated role, so you cannot accept shift "
            f"swaps. This shift requires '{shift_role}'. Ask your facility to "
            "set your role."
        )
    return (
        f"Role mismatch: this shift requires '{shift_role}', you are "
        f"designated '{profile_role}'."
    )


class ShiftListCreateView(APIView):
    """List shifts, and create draft ones.

    Reading serves both audiences: a facility sees its whole rota, drafts
    included, while a professional sees only shifts assigned to them and only
    once published. A draft is the facility thinking aloud — showing staff a
    rota that has not been published would make every unpublished edit look
    like a change to their week.

    Writing stays facility-only.
    """

    permission_classes = [IsFacilityOrProfessional]

    @extend_schema(
        responses={200: ShiftSerializer(many=True)}, summary="List shifts"
    )
    def get(self, request):
        facility = getattr(request, "facility", None)
        shifts = Shift.objects.select_related("professional").order_by("start_time")

        if facility is not None:
            shifts = shifts.filter(facility=facility)
        else:
            shifts = shifts.filter(professional=request.profile, is_published=True)

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
        facility = getattr(request, "facility", None)
        if facility is None:
            # The permission class admits both, so creating needs its own
            # guard. Without it a professional would reach request.facility
            # and get an AttributeError — a 500 where a 403 belongs.
            raise PermissionDenied("Only a facility can create shifts.")

        serializer = ShiftSerializer(
            data=request.data, context={"facility": facility}
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

        # Scoped to the caller's own facility, so a shift belonging to another
        # one is indistinguishable from a shift that does not exist. An
        # unscoped lookup answered 403 "you are not assigned to that shift" for
        # a real id and 404 for a missing one, which let anyone with a Supabase
        # account enumerate shift ids across every facility on the platform.
        # Every other cross-tenant path here answers 404; this one now matches.
        shift = get_object_or_404(Shift, pk=shift_id, facility_id=profile.facility_id)

        # Only the assignee may offer the shift. Within the caller's own
        # facility a 403 is fine — a colleague already sees that rota.
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
    """Swap requests, read by either side.

    A professional sees what they could act on: open offers to the whole
    roster, offers aimed at them, and their own requests whatever the status.

    A facility sees every swap on its own shifts. Per the spec this is
    visibility, not an approval gate — swaps are peer-to-peer and complete
    without management, but a facility that cannot see who is actually working
    a shift has lost track of its own rota.
    """

    permission_classes = [IsFacilityOrProfessional]

    @extend_schema(
        responses={200: ShiftSwapRequestSerializer(many=True)},
        summary="List swap requests",
    )
    def get(self, request):
        facility = getattr(request, "facility", None)
        facility_id = facility.id if facility else request.profile.facility_id

        queryset = (
            ShiftSwapRequest.objects.filter(shift__facility_id=facility_id)
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

        if facility is None:
            # A request aimed at one person is not the rest of the roster's
            # business. The facility is not "the rest of the roster" — it owns
            # the shift, so this narrowing applies to peers only.
            profile = request.profile
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

    # request=None: a bodyless POST otherwise leaves drf-spectacular unable to
    # guess a body, and it drops the endpoint from the schema altogether.
    @extend_schema(
        request=None,
        responses={200: ShiftSwapRequestSerializer},
        summary="Accept a swap",
    )
    def post(self, request, pk):
        profile = request.profile

        # Both scoping rules live in the lookup rather than in checks after it.
        # A custom "no such swap request" message differs from the phrasing
        # get_object_or_404 uses for a missing id, and comparing the two would
        # enumerate swap requests across every facility. Folding them in makes
        # a foreign request, a request aimed at someone else, and a request
        # that never existed all answer identically.
        swap = get_object_or_404(
            ShiftSwapRequest.objects.select_related("shift"),
            Q(target_professional__isnull=True) | Q(target_professional=profile),
            pk=pk,
            shift__facility_id=profile.facility_id,
        )
        if swap.requesting_professional_id == profile.id:
            # Not hidden: you already know your own request exists.
            return Response(
                {"detail": "You cannot accept your own swap request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Opening a swap refuses a shift that has already started, but a
        # request opened in good time can sit pending until the shift begins.
        # Without this, someone could claim a shift already underway an hour
        # after it started, reassigning it retroactively and letting the
        # original assignee off a shift they may well have worked.
        if swap.shift.start_time <= timezone.now():
            return Response(
                {
                    "detail": (
                        "That shift has already started, so it can no longer "
                        "be swapped."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The role guardrail, checked before the claim rather than after it.
        # Order is the whole point: the claim is a one-way door — it flips the
        # request to accepted and there is no un-accept — so a mismatched
        # attempt has to bounce while the request is still pending and still
        # available to someone who is actually designated for the role.
        #
        # 400, not 403 or 404. The enumeration-safe 404s elsewhere in this view
        # hide whether a row exists; this is a different category entirely. The
        # caller may see the request, and telling them exactly why they cannot
        # take it is the useful answer.
        if not role_matches(profile.role, swap.shift.role):
            return Response(
                {"detail": _role_mismatch_message(profile.role, swap.shift.role)},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        request=None,
        responses={200: ShiftSwapRequestSerializer},
        summary="Cancel a swap",
    )
    def post(self, request, pk):
        profile = request.profile
        swap = get_object_or_404(
            ShiftSwapRequest.objects.select_related("shift"),
            pk=pk,
            shift__facility_id=profile.facility_id,
        )
        # Intra-facility, so a 403 is fine here: a colleague can already see
        # this request in the list.
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
