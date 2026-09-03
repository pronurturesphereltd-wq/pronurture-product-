from django.contrib import admin
from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

from facilities.views import (
    FacilityBulkImportView,
    FacilityRegisterView,
    InviteLinkCreateView,
)
from profiles.views import (
    ProfileRegisterViaInviteView,
    ProfileSeedBulkView,
    ProfileSelfRegisterView,
    PushDeviceRegisterView,
)
from rota.views import PublishShiftsView, ShiftListCreateView

urlpatterns = [
    path("admin/", admin.site.urls),
    # --- Facilities ---
    path(
        "api/facilities/register/",
        FacilityRegisterView.as_view(),
        name="facility-register",
    ),
    path(
        "api/facilities/bulk-import/",
        FacilityBulkImportView.as_view(),
        name="facility-bulk-import",
    ),
    path(
        "api/facilities/invite-links/",
        InviteLinkCreateView.as_view(),
        name="facility-invite-links",
    ),
    # --- Profiles ---
    path(
        "api/profiles/seed-bulk/",
        ProfileSeedBulkView.as_view(),
        name="profile-seed-bulk",
    ),
    path(
        "api/profiles/self-register/",
        ProfileSelfRegisterView.as_view(),
        name="profile-self-register",
    ),
    path(
        "api/profiles/register-via-invite/<uuid:token>/",
        ProfileRegisterViaInviteView.as_view(),
        name="profile-register-via-invite",
    ),
    # --- Devices ---
    path(
        "api/devices/register/",
        PushDeviceRegisterView.as_view(),
        name="device-register",
    ),
    # --- Rota ---
    path("api/rota/shifts/", ShiftListCreateView.as_view(), name="rota-shifts"),
    path(
        "api/rota/shifts/publish/",
        PublishShiftsView.as_view(),
        name="rota-shifts-publish",
    ),
    # --- OpenAPI ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
