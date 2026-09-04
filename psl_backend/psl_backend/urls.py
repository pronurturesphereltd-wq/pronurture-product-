from django.contrib import admin
from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

from core.views import WhoAmIView

from compliance.views import (
    ComplianceAlertListView,
    ComplianceAlertResolveView,
)
from facilities.views import (
    BulkImportStatusView,
    FacilityBulkImportView,
    FacilityRegisterView,
    FacilityStaffView,
    InviteLinkCreateView,
)
from leave.views import (
    LeaveApplicationListCreateView,
    LeaveApproveView,
    LeaveDeclineView,
)
from profiles.views import (
    ProfileRegisterViaInviteView,
    ProfileSeedBulkView,
    ProfileSelfRegisterView,
    PushDeviceRegisterView,
)
from rota.views import (
    PublishShiftsView,
    ShiftListCreateView,
    ShiftSwapRequestCreateView,
    SwapRequestAcceptView,
    SwapRequestCancelView,
    SwapRequestListView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # --- Identity ---
    path("api/me/", WhoAmIView.as_view(), name="whoami"),
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
        "api/facilities/bulk-import/<str:task_id>/",
        BulkImportStatusView.as_view(),
        name="facility-bulk-import-status",
    ),
    path(
        "api/facilities/staff/",
        FacilityStaffView.as_view(),
        name="facility-staff",
    ),
    path(
        "api/facilities/invite-links/",
        InviteLinkCreateView.as_view(),
        name="facility-invite-links",
    ),
    # Facility-facing, so routed here; the model and its sweep live in
    # compliance/.
    path(
        "api/facilities/compliance-alerts/",
        ComplianceAlertListView.as_view(),
        name="compliance-alerts",
    ),
    path(
        "api/facilities/compliance-alerts/<int:pk>/resolve/",
        ComplianceAlertResolveView.as_view(),
        name="compliance-alert-resolve",
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
    path(
        "api/rota/shifts/<int:shift_id>/swap-request/",
        ShiftSwapRequestCreateView.as_view(),
        name="rota-swap-request-create",
    ),
    path(
        "api/rota/swap-requests/",
        SwapRequestListView.as_view(),
        name="rota-swap-requests",
    ),
    path(
        "api/rota/swap-requests/<int:pk>/accept/",
        SwapRequestAcceptView.as_view(),
        name="rota-swap-request-accept",
    ),
    path(
        "api/rota/swap-requests/<int:pk>/cancel/",
        SwapRequestCancelView.as_view(),
        name="rota-swap-request-cancel",
    ),
    # --- Leave ---
    path(
        "api/leave/applications/",
        LeaveApplicationListCreateView.as_view(),
        name="leave-applications",
    ),
    path(
        "api/leave/applications/<int:pk>/approve/",
        LeaveApproveView.as_view(),
        name="leave-application-approve",
    ),
    path(
        "api/leave/applications/<int:pk>/decline/",
        LeaveDeclineView.as_view(),
        name="leave-application-decline",
    ),
    # --- OpenAPI ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
