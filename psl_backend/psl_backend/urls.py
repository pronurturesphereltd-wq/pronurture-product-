from django.contrib import admin
from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

from facilities.views import FacilityRegisterView
from profiles.views import ProfileSeedBulkView, ProfileSelfRegisterView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Public API — authenticated with Supabase-issued JWTs.
    path(
        "api/facilities/register/",
        FacilityRegisterView.as_view(),
        name="facility-register",
    ),
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
    # OpenAPI schema
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
