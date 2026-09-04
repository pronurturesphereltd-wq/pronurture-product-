from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_q.tasks import async_task
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsFacility, IsFacilityOrProfessional

from .models import LeaveApplication
from .serializers import LeaveApplicationSerializer


class LeaveApplicationListCreateView(APIView):
    """Submit leave (professional) and read the queue (either side).

    One endpoint, two audiences. A facility sees every application from its own
    roster — this is the approval queue. A professional sees only their own,
    which is how they learn a decision was made: per the spec, the status must
    be readable with a plain GET rather than depending on a push arriving.
    """

    permission_classes = [IsFacilityOrProfessional]

    @extend_schema(
        responses={200: LeaveApplicationSerializer(many=True)},
        summary="List leave applications",
    )
    def get(self, request):
        facility = getattr(request, "facility", None)
        queryset = LeaveApplication.objects.select_related(
            "professional"
        ).order_by("-created_at")

        if facility is not None:
            queryset = queryset.filter(professional__facility=facility)
        else:
            queryset = queryset.filter(professional=request.profile)

        requested_status = request.query_params.get("status")
        if requested_status:
            queryset = queryset.filter(status=requested_status)

        return Response(LeaveApplicationSerializer(queryset, many=True).data)

    @extend_schema(
        request=LeaveApplicationSerializer,
        responses={201: LeaveApplicationSerializer},
        summary="Submit a leave application",
    )
    def post(self, request):
        profile = getattr(request, "profile", None)
        if profile is None:
            raise PermissionDenied(
                "Only a professional can submit a leave application."
            )
        if profile.facility_id is None:
            # Nobody would ever see it: the queue is scoped by facility, so an
            # unattached profile's application would sit in no queue at all.
            return Response(
                {
                    "detail": (
                        "Your profile is not attached to a facility, so there "
                        "is nobody to approve leave."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = LeaveApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start = serializer.validated_data["start_date"]
        end = serializer.validated_data["end_date"]

        # An open or approved application already covering these dates means a
        # duplicate submission, which would otherwise show up in the facility's
        # queue twice and could be approved twice. Declined ones do not block:
        # re-applying after a refusal is legitimate.
        overlapping = LeaveApplication.objects.filter(
            professional=profile,
            status__in=[
                LeaveApplication.Status.SUBMITTED,
                LeaveApplication.Status.APPROVED,
            ],
            start_date__lte=end,
            end_date__gte=start,
        ).first()
        if overlapping is not None:
            return Response(
                {
                    "detail": (
                        f"You already have a {overlapping.status} application "
                        f"covering {overlapping.start_date} to "
                        f"{overlapping.end_date}."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        application = serializer.save(professional=profile)
        return Response(
            LeaveApplicationSerializer(application).data,
            status=status.HTTP_201_CREATED,
        )


class LeaveDecisionView(APIView):
    """Shared base for approve and decline.

    The decision is a conditional UPDATE filtered on `status='submitted'`, the
    same shape as the swap claim: two facility admins both hitting approve, or
    one hitting approve and the other decline, cannot both take effect. The
    second sees zero rows affected and a 409 rather than silently overwriting
    the first decision.
    """

    permission_classes = [IsFacility]
    decision = None

    def post(self, request, pk):
        # Scoped in the lookup itself rather than checked afterwards. A custom
        # "no such application" message is still an oracle: it differs from the
        # phrasing get_object_or_404 uses for an id that does not exist, so a
        # caller comparing bodies could enumerate every leave application on
        # the platform. Filtering here makes both cases the same 404.
        application = get_object_or_404(
            LeaveApplication.objects.select_related("professional"),
            pk=pk,
            professional__facility_id=request.facility.id,
        )

        now = timezone.now()
        with transaction.atomic():
            decided = LeaveApplication.objects.filter(
                pk=pk, status=LeaveApplication.Status.SUBMITTED
            ).update(status=self.decision, decided_at=now)
            if decided != 1:
                return Response(
                    {"detail": "That application has already been decided."},
                    status=status.HTTP_409_CONFLICT,
                )
            application.refresh_from_db()
            # Re-saved so the decision reaches the audit trail. The conditional
            # UPDATE above cannot write history, and it has to stay a single
            # statement to be the race guard.
            application.save()

        # Best-effort: the professional may have no registered device, and the
        # decision is readable from the list endpoint either way.
        async_task(
            "rota.tasks.send_push_notification",
            professional_id=application.professional_id,
            title=f"Leave {application.status}",
            body=(
                f"{application.start_date:%d %b} – {application.end_date:%d %b}: "
                f"{application.status}"
            ),
            data={"leave_application_id": application.id, "kind": "leave_decision"},
            task_name=f"leave-decision-{application.id}",
        )

        return Response(LeaveApplicationSerializer(application).data)


class LeaveApproveView(LeaveDecisionView):
    decision = LeaveApplication.Status.APPROVED

    # request=None matters: without it drf-spectacular cannot guess a body for
    # a bodyless POST and drops the endpoint from the schema entirely, so it
    # never appears in /api/docs/.
    @extend_schema(
        request=None,
        responses={200: LeaveApplicationSerializer},
        summary="Approve a leave application",
    )
    def post(self, request, pk):
        return super().post(request, pk)


class LeaveDeclineView(LeaveDecisionView):
    decision = LeaveApplication.Status.DECLINED

    @extend_schema(
        request=None,
        responses={200: LeaveApplicationSerializer},
        summary="Decline a leave application",
    )
    def post(self, request, pk):
        return super().post(request, pk)
