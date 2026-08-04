"""Root URL configuration for trazeiq_backend."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from trazeiq_backend.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    # Versioned from the first endpoint, per Agent.md conventions
    path("api/v1/health/", health, name="health"),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/organizations/", include("apps.organizations.urls")),
    path("api/v1/projects/", include("apps.projects.urls")),
    path("api/v1/events/", include("apps.events.urls")),
    path("api/v1/incidents/", include("apps.incidents.urls")),
    # Unversioned alias for the health probe
    path("api/health/", health),
]

if settings.ENABLE_API_SCHEMA:
    urlpatterns += [
        path(
            "api/v1/schema/",
            SpectacularAPIView.as_view(),
            name="schema",
        ),
        path(
            "api/v1/docs/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
        path(
            "api/v1/redoc/",
            SpectacularRedocView.as_view(url_name="schema"),
            name="redoc",
        ),
    ]