"""Root URL configuration for trazeiq_backend."""

from django.contrib import admin
from django.urls import path

from trazeiq_backend.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    # Versioned from the first endpoint, per Agent.md conventions
    path("api/v1/health/", health, name="health"),
    # Unversioned alias for the health probe
    path("api/health/", health),
]
