"""Phase 1F: read-only incident views powering the dashboard's static pages.

Incident list/detail/timeline — all org-scoped, JWT auth, no writes yet
(PATCH/analyze/comments arrive in later phases).
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from uuid import UUID

from django.utils import timezone

from apps.realtime.services import publish_incident_event
from trazeiq_backend.responses import api_success, envelope_schema

from .models import Incident, TimelineEntry
from .selectors import (
    get_incident_for_user,
    latest_events_by_id,
    list_incident_timeline,
    list_incidents_for_user,
)
from .serializers import (
    IncidentOutputSerializer,
    TimelineEntryOutputSerializer,
)

INCIDENT_NOT_FOUND = "This incident does not exist."

VALID_FILTERS = {
    "status": {choice[0] for choice in Incident.Status.choices},
    "severity": {choice[0] for choice in Incident.Severity.choices},
}


def _incident_schema(name: str):
    return inline_serializer(
        name,
        fields={"incident": IncidentOutputSerializer()},
    )


class IncidentListView(APIView):
    """GET /api/incidents/ — org-scoped list with status/severity/project
    filters, newest-activity first."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_list",
        summary="List incidents",
        description=(
            "Incidents from every project in the caller's organizations, "
            "most recently-active first. Filter by status, severity or "
            "project."
        ),
        parameters=[
            inline_serializer(
                "IncidentListQuery",
                fields={
                    "status": serializers.ChoiceField(
                        choices=Incident.Status.choices, required=False
                    ),
                    "severity": serializers.ChoiceField(
                        choices=Incident.Severity.choices, required=False
                    ),
                    "project": serializers.UUIDField(required=False),
                },
            )
        ],
        responses={
            200: envelope_schema(
                "IncidentListOk",
                payload=inline_serializer(
                    "IncidentListData",
                    fields={
                        "incidents": IncidentOutputSerializer(many=True)
                    },
                ),
            ),
            400: envelope_schema("IncidentListValidation", error=True),
            401: envelope_schema("IncidentListUnauthorized", error=True),
        },
    )
    def get(self, request):
        query = request.query_params
        invalid = {
            key: query[key]
            for key, choices in VALID_FILTERS.items()
            if key in query and query[key] not in choices
        }
        if invalid:
            raise serializers.ValidationError(
                {
                    key: f"Must be one of: {', '.join(sorted(choices))}."
                    for key, choices in VALID_FILTERS.items()
                    if key in invalid
                }
            )

        project_id = query.get("project")
        if project_id is not None:
            try:
                project_id = UUID(project_id)
            except (TypeError, ValueError, AttributeError):
                raise serializers.ValidationError(
                    {"project": "Must be a valid UUID."}
                )

        incidents = list_incidents_for_user(
            request.user,
            status=query.get("status"),
            severity=query.get("severity"),
            project_id=project_id,
        )
        events = latest_events_by_id(incidents)
        return api_success(
            data={
                "incidents": IncidentOutputSerializer(
                    incidents, many=True, context={"events": events}
                ).data
            }
        )


class IncidentDetailView(APIView):
    """GET /api/incidents/{id}/ — one incident with its group summary and the
    latest raw occurrence; 404 if not in the caller's organization."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_retrieve",
        summary="Incident detail",
        responses={
            200: envelope_schema(
                "IncidentDetailOk",
                payload=_incident_schema("IncidentDetailData"),
            ),
            401: envelope_schema("IncidentDetailUnauthorized", error=True),
            404: envelope_schema("IncidentDetailNotFound", error=True),
        },
    )
    def get(self, request, incident_id: UUID):
        incident = get_incident_for_user(incident_id, request.user)
        if incident is None:
            raise NotFound(INCIDENT_NOT_FOUND)
        events = latest_events_by_id([incident])
        return api_success(
            data={
                "incident": IncidentOutputSerializer(
                    incident, context={"events": events}
                ).data
            }
        )


class IncidentTimelineView(APIView):
    """GET /api/incidents/{id}/timeline/ — the occurrence feed.

    Phase 1F serves static ``kind="event"`` rows (the raw occurrences of the
    incident's error group, oldest first); comments/status changes/AI
    analysis extend the feed in later phases.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["incidents"],
        operation_id="incident_timeline",
        summary="Incident timeline",
        description=(
            "Chronological occurrence feed for the incident's error group. "
            "Future phases add comment / status_change / ai_analysis entry "
            "kinds to the same shape."
        ),
        responses={
            200: envelope_schema(
                "IncidentTimelineOk",
                payload=inline_serializer(
                    "IncidentTimelineData",
                    fields={
                        "entries": TimelineEntryOutputSerializer(many=True)
                    },
                ),
            ),
            401: envelope_schema(
                "IncidentTimelineUnauthorized", error=True
            ),
            404: envelope_schema("IncidentTimelineNotFound", error=True),
        },
    )
    def get(self, request, incident_id: UUID):
        incident = get_incident_for_user(incident_id, request.user)
        if incident is None:
            raise NotFound(INCIDENT_NOT_FOUND)
        events = list_incident_timeline(incident)
        return api_success(
            data={
                "entries": [
                    {
                        "id": event.id,
                        "kind": "event",
                        "level": event.level,
                        "message": event.message,
                        "environment": event.environment,
                        "service": event.service,
                        "created_at": event.created_at,
                    }
                    for event in events
                ]
            }
        )


class IncidentResolveView(APIView):
    """POST /api/incidents/{id}/resolve/ — mark an incident resolved.

    Phase 3A stub: flips the status, records the resolved-at timestamp and a
    ``status_change`` timeline entry, and pushes ``incident.resolved`` on the
    project's Pusher channel. Reassignment/reopen/comment flows arrive in
    Phase 4B.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["incidents"],
        operation_id="incident_resolve",
        summary="Resolve an incident",
        request=None,
        responses={
            200: envelope_schema(
                "IncidentResolveOk",
                payload=_incident_schema("IncidentResolveData"),
            ),
            401: envelope_schema("IncidentResolveUnauthorized", error=True),
            404: envelope_schema("IncidentResolveNotFound", error=True),
        },
    )
    def post(self, request, incident_id: UUID):
        incident = get_incident_for_user(incident_id, request.user)
        if incident is None:
            raise NotFound(INCIDENT_NOT_FOUND)

        if incident.status != Incident.Status.RESOLVED:
            incident.status = Incident.Status.RESOLVED
            incident.resolved_at = timezone.now()
            incident.save(update_fields=["status", "resolved_at"])
            TimelineEntry.objects.create(
                incident=incident,
                kind=TimelineEntry.Kind.STATUS_CHANGE,
                content="Incident marked resolved",
                actor=request.user,
            )
            publish_incident_event(incident, event_name="incident.resolved")

        events = latest_events_by_id([incident])
        return api_success(
            data={
                "incident": IncidentOutputSerializer(
                    incident, context={"events": events}
                ).data
            }
        )


__all__ = [
    "IncidentDetailView",
    "IncidentListView",
    "IncidentResolveView",
    "IncidentTimelineView",
    "INCIDENT_NOT_FOUND",
]
