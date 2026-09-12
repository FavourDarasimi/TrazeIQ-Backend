from django.urls import path

from .views import (
    IncidentBulkAssignView,
    IncidentBulkIgnoreView,
    IncidentBulkResolveView,
    IncidentBulkUpdateView,
    IncidentBulkView,
    IncidentCommentView,
    IncidentDetailView,
    IncidentListView,
    IncidentResolveView,
    IncidentTimelineView,
)

urlpatterns = [
    path("", IncidentListView.as_view(), name="incident-list"),
    path("bulk/", IncidentBulkView.as_view(), name="incident-bulk"),
    path(
        "bulk-update/",
        IncidentBulkUpdateView.as_view(),
        name="incident-bulk-update",
    ),
    path(
        "bulk-resolve/",
        IncidentBulkResolveView.as_view(),
        name="incident-bulk-resolve",
    ),
    path(
        "bulk-assign/",
        IncidentBulkAssignView.as_view(),
        name="incident-bulk-assign",
    ),
    path(
        "bulk-ignore/",
        IncidentBulkIgnoreView.as_view(),
        name="incident-bulk-ignore",
    ),
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
        "<uuid:incident_id>/comments/",
        IncidentCommentView.as_view(),
        name="incident-comments",
    ),
    path(
        "<uuid:incident_id>/resolve/",
        IncidentResolveView.as_view(),
        name="incident-resolve",
    ),
]