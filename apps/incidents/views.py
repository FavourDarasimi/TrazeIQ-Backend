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

from apps.alerts.services import enqueue_alert_evaluation
from apps.auditlog.models import AuditAction
from apps.auditlog.services import record_audit_log
from apps.realtime.services import publish_incident_event
from trazeiq_backend.responses import api_success, envelope_schema

from .models import Incident, TimelineEntry
from .permissions import IsIncidentDeveloperOrAbove
from .selectors import (
    get_incident_for_user,
    latest_events_by_id,
    list_incident_timeline,
    list_incidents_for_user,
)
from .serializers import (
    CommentInputSerializer,
    IncidentOutputSerializer,
    IncidentUpdateSerializer,
    TimelineEntryOutputSerializer,
)
from .services import add_comment, update_incident

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
    latest raw occurrence; 404 if not in the caller's organization.

    PATCH /api/incidents/{id}/ — update status/severity/assignment
    (developer or above; viewers are read-only).
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), IsIncidentDeveloperOrAbove()]
        return super().get_permissions()

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

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_update",
        summary="Update an incident",
        description=(
            "Update status, severity and/or assignment. Only organization "
            "members can be assigned. Any effective change appends a "
            "``status_change`` timeline entry and pushes an "
            "``incident.updated`` realtime event."
        ),
        request=IncidentUpdateSerializer,
        responses={
            200: envelope_schema(
                "IncidentUpdateOk",
                payload=_incident_schema("IncidentUpdateData"),
            ),
            400: envelope_schema("IncidentUpdateValidation", error=True),
            401: envelope_schema("IncidentUpdateUnauthorized", error=True),
            403: envelope_schema("IncidentUpdateForbidden", error=True),
            404: envelope_schema("IncidentUpdateNotFound", error=True),
        },
    )
    def patch(self, request, incident_id: UUID):
        incident = get_incident_for_user(incident_id, request.user)
        if incident is None:
            raise NotFound(INCIDENT_NOT_FOUND)

        serializer = IncidentUpdateSerializer(
            data=request.data,
            partial=True,
            context={"organization_id": incident.project.organization_id},
        )
        serializer.is_valid(raise_exception=True)

        # Only the fields actually present in the request body are applied —
        # absent fields must stay untouched (a PATCH of {status} alone must
        # not clear severity or the assignment).
        updates = {
            key: serializer.validated_data[key]
            for key in ("status", "severity", "assigned_to")
            if key in serializer.validated_data
        }
        incident = update_incident(incident, actor=request.user, **updates)
        publish_incident_event(incident, event_name="incident.updated")
        # Phase 4C: a PATCH may have changed severity/status — re-evaluate
        # alert rules async (cooldown dedups the repeats). Best-effort.
        enqueue_alert_evaluation(incident.pk)

        events = latest_events_by_id([incident])
        return api_success(
            data={
                "incident": IncidentOutputSerializer(
                    incident, context={"events": events}
                ).data
            }
        )


class IncidentTimelineView(APIView):
    """GET /api/incidents/{id}/timeline/ — the incident's full history.

    Phase 4B: mixes all four TimelineEntry kinds — event occurrences (the
    raw events of the error group), comments, status changes, and AI
    analyses — into one chronological feed, oldest first.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["incidents"],
        operation_id="incident_timeline",
        summary="Incident timeline",
        description=(
            "Chronological history for the incident: event occurrences, "
            "comments, status changes and AI analyses. Event rows carry "
            "level/message/environment/service; the other kinds carry "
            "content and an optional actor email."
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
        return api_success(
            data={
                "entries": TimelineEntryOutputSerializer(
                    list_incident_timeline(incident), many=True
                ).data
            }
        )


class IncidentCommentView(APIView):
    """POST /api/incidents/{id}/comments/ — append a comment to the timeline.

    Developer or above. The entry appears in the timeline immediately with
    the caller as actor; an ``incident.updated`` realtime event is pushed so
    other sessions viewing the incident pick the comment up live (Phase 4F).
    """

    permission_classes = [IsAuthenticated, IsIncidentDeveloperOrAbove]

    @extend_schema(
        tags=["incidents"],
        operation_id="incident_create_comment",
        summary="Comment on an incident",
        description=(
            "Append a comment to the incident's timeline (developer or "
            "above; viewers are read-only)."
        ),
        request=CommentInputSerializer,
        responses={
            201: envelope_schema(
                "IncidentCommentCreated",
                payload=inline_serializer(
                    "IncidentCommentData",
                    fields={"entry": TimelineEntryOutputSerializer()},
                ),
            ),
            400: envelope_schema("IncidentCommentValidation", error=True),
            401: envelope_schema("IncidentCommentUnauthorized", error=True),
            403: envelope_schema("IncidentCommentForbidden", error=True),
            404: envelope_schema("IncidentCommentNotFound", error=True),
        },
    )
    def post(self, request, incident_id: UUID):
        incident = get_incident_for_user(incident_id, request.user)
        if incident is None:
            raise NotFound(INCIDENT_NOT_FOUND)

        serializer = CommentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = add_comment(
            incident,
            content=serializer.validated_data["content"],
            actor=request.user,
        )
        publish_incident_event(incident, event_name="incident.updated")
        return api_success(
            data={"entry": TimelineEntryOutputSerializer(entry).data},
            status=status.HTTP_201_CREATED,
        )


class IncidentResolveView(APIView):
    """POST /api/incidents/{id}/resolve/ — mark an incident resolved.

    Flips the status, records the resolved-at timestamp and a
    ``status_change`` timeline entry, and pushes ``incident.resolved`` on the
    project's Pusher channel (idempotent — resolving an already-resolved
    incident is a no-op). Status/severity/assignment updates via PATCH and
    timeline comments arrive in Phases 4A/4B.
    """

    permission_classes = [IsAuthenticated, IsIncidentDeveloperOrAbove]

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
            record_audit_log(
                actor=request.user,
                organization=incident.project.organization,
                action=AuditAction.INCIDENT_RESOLVED,
                target=f"Resolved incident '{incident.error_group.title}'",
            )

        events = latest_events_by_id([incident])
        return api_success(
            data={
                "incident": IncidentOutputSerializer(
                    incident, context={"events": events}
                ).data
            }
        )


__all__ = [
    "IncidentCommentView",
    "IncidentDetailView",
    "IncidentListView",
    "IncidentResolveView",
    "IncidentTimelineView",
    "INCIDENT_NOT_FOUND",
]
