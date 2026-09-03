from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Facility
from .serializers import FacilityRegistrationSerializer


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
