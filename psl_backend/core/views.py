"""Identity for the signed-in caller.

A Supabase token proves who someone is, not what they are. Until now every
endpoint answered that question privately, inside a permission class, and the
frontend had no way to ask it — so the facility app rendered facility controls
to anyone with a valid token, including professionals, who then collected 403s
from half the page.
"""

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsFacilityOrProfessional


class WhoAmIView(APIView):
    """What kind of PSL account is this token, and what may it do?

    `kind` is what the frontend routes on. A 403 here is meaningful rather
    than an error to hide: it means the token is valid but PSL has no record
    for it, which is exactly what a facility awaiting approval or a stranger
    who signed up looks like. The permission class's message says which.
    """

    permission_classes = [IsFacilityOrProfessional]

    @extend_schema(responses={200: None}, summary="Who is this token?")
    def get(self, request):
        facility = getattr(request, "facility", None)
        if facility is not None:
            return Response(
                {
                    "kind": "facility",
                    "facility": {
                        "id": facility.id,
                        "name": facility.name,
                        "status": facility.status,
                    },
                    "profile": None,
                }
            )

        profile = request.profile
        return Response(
            {
                "kind": "professional",
                "facility": (
                    {
                        "id": profile.facility_id,
                        "name": profile.facility.name,
                        "status": profile.facility.status,
                    }
                    if profile.facility_id
                    else None
                ),
                "profile": {
                    "id": profile.id,
                    "full_name": profile.full_name,
                    "email": profile.email,
                    # Surfaced because it gates swap acceptance. A professional
                    # refused a swap should be able to see why without guessing.
                    "role": profile.role,
                    "verification_state": profile.verification_state,
                },
            }
        )
