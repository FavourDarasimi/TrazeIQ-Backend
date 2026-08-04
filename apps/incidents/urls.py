from django.urls import path

from .views import (
    IncidentDetailView,
    IncidentListView,
    IncidentTimelineView,
)

urlpatterns = [
    path("", IncidentListView.as_view(), name="incident-list"),
    path(
        "<int:incident_id>/",
        IncidentDetailView.as_view(),
        name="incident-detail",
    ),
    path(
        "<int:incident_id>/timeline/",
        IncidentTimelineView.as_view(),
        name="incident-timeline",
    ),
]