from django.urls import path

from apps.ai.views import IncidentAnalysisView, IncidentAnalyzeView

from .views import (
    IncidentDetailView,
    IncidentListView,
    IncidentTimelineView,
)

urlpatterns = [
    path("", IncidentListView.as_view(), name="incident-list"),
    path(
        "<uuid:incident_id>/",
        IncidentDetailView.as_view(),
        name="incident-detail",
    ),
    path(
        "<uuid:incident_id>/timeline/",
        IncidentTimelineView.as_view(),
        name="incident-timeline",
    ),
    path(
        "<uuid:incident_id>/analyze/",
        IncidentAnalyzeView.as_view(),
        name="incident-analyze",
    ),
    path(
        "<uuid:incident_id>/analysis/",
        IncidentAnalysisView.as_view(),
        name="incident-analysis",
    ),
]