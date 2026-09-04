"""Mapping a verified Supabase identity onto a PSL record.

A Supabase JWT proves who the caller is, not what they are. These permissions
do the second half: look up the Facility or Profile that claims that
`supabase_user_id` and attach it to the request, so views never have to repeat
the lookup or trust a facility id supplied in the request body.
"""

from django.conf import settings
from rest_framework.permissions import BasePermission


class IsFacility(BasePermission):
    """Caller owns a Facility record. Attaches `request.facility`."""

    message = "No facility is registered for this account."

    def has_permission(self, request, view):
        from facilities.models import Facility

        supabase_user_id = getattr(request.user, "supabase_user_id", None)
        if not supabase_user_id:
            return False

        facility = Facility.objects.filter(
            supabase_user_id=supabase_user_id
        ).first()
        if facility is None:
            return False

        # Provisioning auth accounts and emailing staff are things an
        # unapproved facility should not be able to do. Off by default only if
        # explicitly disabled for local testing.
        if settings.REQUIRE_APPROVED_FACILITY and facility.status != Facility.Status.APPROVED:
            self.message = (
                f"This facility is {facility.status} and cannot perform this "
                "action until PSL approves it."
            )
            return False

        request.facility = facility
        return True


class IsProfessional(BasePermission):
    """Caller owns a Profile record. Attaches `request.profile`."""

    message = "No professional profile is registered for this account."

    def has_permission(self, request, view):
        from profiles.models import Profile

        supabase_user_id = getattr(request.user, "supabase_user_id", None)
        if not supabase_user_id:
            return False

        profile = Profile.objects.filter(supabase_user_id=supabase_user_id).first()
        if profile is None:
            return False

        request.profile = profile
        return True


class IsFacilityOrProfessional(BasePermission):
    """Either side of the relationship, for endpoints both audiences read.

    The leave list is one queryset with two meanings: a facility's approval
    queue, and a professional's own history. Rather than duplicate the
    endpoint, this attaches whichever record the caller owns and the view
    branches on which one arrived. Views must read `request.facility` and
    `request.profile` with `getattr` — exactly one of them is set.

    Written out rather than composed as `IsFacility | IsProfessional` so the
    denial message names both possibilities; DRF's OR falls back to the generic
    "you do not have permission" and loses whatever each class had to say.
    """

    message = (
        "No facility or professional profile is registered for this account."
    )

    def has_permission(self, request, view):
        facility_check = IsFacility()
        if facility_check.has_permission(request, view):
            return True
        # A facility that exists but is not yet approved is a different
        # situation from an account PSL has never seen, and its message is more
        # useful than this class's generic one.
        if getattr(request.user, "supabase_user_id", None):
            from facilities.models import Facility

            if Facility.objects.filter(
                supabase_user_id=request.user.supabase_user_id
            ).exists():
                self.message = facility_check.message
                return False

        return IsProfessional().has_permission(request, view)
