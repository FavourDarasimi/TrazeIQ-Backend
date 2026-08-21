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
from .permissions import IsBulkIncidentDeveloperOrAbove, IsIncidentDeveloperOrAbove
from .selectors import (
    get_incident_for_user,
    get_updatable_incidents_for_user,
    latest_events_by_id,
    list_incident_timeline,
    list_incidents_for_user,
)
from .serializers import (
    BulkUpdateSerializer,
    CommentInputSerializer,
    IncidentOutputSerializer,
    IncidentUpdateSerializer,
    TimelineEntryOutputSerializer,
)
from .services import add_comment, bulk_update_incidents, update_incident

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
            # Best-effort inbox fan-out — never fails the resolve action.
            from apps.notifications.services import notify_incident_resolved

            notify_incident_resolved(incident, actor_id=request.user.pk)

        events = latest_events_by_id([incident])
        return api_success(
            data={
                "incident": IncidentOutputSerializer(
                    incident, context={"events": events}
                ).data
            }
        )


class IncidentBulkUpdateView(APIView):
    """POST /api/v1/incidents/bulk-update/ — apply status/severity/assignment
    updates to multiple incidents at once."""

    permission_classes = [IsAuthenticated, IsBulkIncidentDeveloperOrAbove]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_bulk_update",
        summary="Bulk update incidents",
        request=BulkUpdateSerializer,
        responses={
            200: envelope_schema(
                "IncidentBulkUpdateOk",
                payload=inline_serializer(
                    "IncidentBulkUpdateData",
                    fields={
                        "updated_count": serializers.IntegerField(),
                        "incidents": IncidentOutputSerializer(many=True),
                    },
                ),
            ),
            400: envelope_schema("IncidentBulkUpdateValidation", error=True),
            401: envelope_schema("IncidentBulkUpdateUnauthorized", error=True),
            403: envelope_schema("IncidentBulkUpdateForbidden", error=True),
        },
    )
    def post(self, request):
        raw_ids = request.data.get("incident_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            raise serializers.ValidationError(
                {"incident_ids": "A non-empty list of UUIDs is required."}
            )

        valid_uuids = []
        for item in raw_ids:
            try:
                valid_uuids.append(UUID(str(item)))
            except (TypeError, ValueError, AttributeError):
                raise serializers.ValidationError(
                    {"incident_ids": f"Invalid UUID: {item}"}
                )

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return api_success(
                data={"updated_count": 0, "incidents": []},
                message="No updatable incidents found.",
            )

        org_ids = {inc.project.organization_id for inc in incidents}
        serializer = BulkUpdateSerializer(
            data=request.data,
            context={"organization_ids": org_ids},
        )
        serializer.is_valid(raise_exception=True)

        updates = {
            key: serializer.validated_data[key]
            for key in ("status", "severity", "assigned_to")
            if key in serializer.validated_data
        }

        updated = bulk_update_incidents(
            incidents, actor=request.user, **updates
        )

        for incident in updated:
            publish_incident_event(incident, event_name="incident.updated")
            enqueue_alert_evaluation(incident.pk)

        if updated:
            first_org = updated[0].project.organization
            change_summary = ", ".join(f"{k}={v}" for k, v in updates.items())
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=f"Bulk updated {len(updated)} incident(s): {change_summary}",
            )

        events = latest_events_by_id(updated)
        return api_success(
            data={
                "updated_count": len(updated),
                "incidents": IncidentOutputSerializer(
                    updated, many=True, context={"events": events}
                ).data,
            }
        )


class IncidentBulkResolveView(APIView):
    """POST /api/v1/incidents/bulk-resolve/ — mark multiple incidents resolved."""

    permission_classes = [IsAuthenticated, IsBulkIncidentDeveloperOrAbove]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_bulk_resolve",
        summary="Bulk resolve incidents",
        request=inline_serializer(
            "BulkResolveRequest",
            fields={"incident_ids": serializers.ListField(child=serializers.UUIDField())},
        ),
        responses={
            200: envelope_schema(
                "IncidentBulkResolveOk",
                payload=inline_serializer(
                    "IncidentBulkResolveData",
                    fields={
                        "updated_count": serializers.IntegerField(),
                        "incidents": IncidentOutputSerializer(many=True),
                    },
                ),
            ),
            400: envelope_schema("IncidentBulkResolveValidation", error=True),
            401: envelope_schema("IncidentBulkResolveUnauthorized", error=True),
            403: envelope_schema("IncidentBulkResolveForbidden", error=True),
        },
    )
    def post(self, request):
        raw_ids = request.data.get("incident_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            raise serializers.ValidationError(
                {"incident_ids": "A non-empty list of UUIDs is required."}
            )

        valid_uuids = []
        for item in raw_ids:
            try:
                valid_uuids.append(UUID(str(item)))
            except (TypeError, ValueError, AttributeError):
                raise serializers.ValidationError(
                    {"incident_ids": f"Invalid UUID: {item}"}
                )

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return api_success(
                data={"updated_count": 0, "incidents": []},
                message="No updatable incidents found.",
            )

        updated = bulk_update_incidents(
            incidents, actor=request.user, status=Incident.Status.RESOLVED
        )

        for incident in updated:
            publish_incident_event(incident, event_name="incident.resolved")
            enqueue_alert_evaluation(incident.pk)

        if updated:
            first_org = updated[0].project.organization
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=f"Bulk resolved {len(updated)} incident(s)",
            )

        events = latest_events_by_id(updated)
        return api_success(
            data={
                "updated_count": len(updated),
                "incidents": IncidentOutputSerializer(
                    updated, many=True, context={"events": events}
                ).data,
            }
        )


class IncidentBulkAssignView(APIView):
    """POST /api/v1/incidents/bulk-assign/ — assign multiple incidents to a member or unassign."""

    permission_classes = [IsAuthenticated, IsBulkIncidentDeveloperOrAbove]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_bulk_assign",
        summary="Bulk assign incidents",
        request=inline_serializer(
            "BulkAssignRequest",
            fields={
                "incident_ids": serializers.ListField(child=serializers.UUIDField()),
                "assigned_to": serializers.UUIDField(allow_null=True, required=False),
            },
        ),
        responses={
            200: envelope_schema(
                "IncidentBulkAssignOk",
                payload=inline_serializer(
                    "IncidentBulkAssignData",
                    fields={
                        "updated_count": serializers.IntegerField(),
                        "incidents": IncidentOutputSerializer(many=True),
                    },
                ),
            ),
            400: envelope_schema("IncidentBulkAssignValidation", error=True),
            401: envelope_schema("IncidentBulkAssignUnauthorized", error=True),
            403: envelope_schema("IncidentBulkAssignForbidden", error=True),
        },
    )
    def post(self, request):
        raw_ids = request.data.get("incident_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            raise serializers.ValidationError(
                {"incident_ids": "A non-empty list of UUIDs is required."}
            )

        valid_uuids = []
        for item in raw_ids:
            try:
                valid_uuids.append(UUID(str(item)))
            except (TypeError, ValueError, AttributeError):
                raise serializers.ValidationError(
                    {"incident_ids": f"Invalid UUID: {item}"}
                )

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return api_success(
                data={"updated_count": 0, "incidents": []},
                message="No updatable incidents found.",
            )

        org_ids = {inc.project.organization_id for inc in incidents}
        serializer = BulkUpdateSerializer(
            data={"incident_ids": raw_ids, "assigned_to": request.data.get("assigned_to")},
            context={"organization_ids": org_ids},
        )
        serializer.is_valid(raise_exception=True)

        updated = bulk_update_incidents(
            incidents,
            actor=request.user,
            assigned_to=serializer.validated_data.get("assigned_to"),
        )

        for incident in updated:
            publish_incident_event(incident, event_name="incident.updated")
            enqueue_alert_evaluation(incident.pk)

        if updated:
            first_org = updated[0].project.organization
            assignee = serializer.validated_data.get("assigned_to")
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=f"Bulk assigned {len(updated)} incident(s) to {assignee.email if assignee else 'unassigned'}",
            )

        events = latest_events_by_id(updated)
        return api_success(
            data={
                "updated_count": len(updated),
                "incidents": IncidentOutputSerializer(
                    updated, many=True, context={"events": events}
                ).data,
            }
        )


__all__ = [
    "IncidentBulkAssignView",
    "IncidentBulkResolveView",
    "IncidentBulkUpdateView",
    "IncidentCommentView",
    "IncidentDetailView",
    "IncidentListView",
    "IncidentResolveView",
    "IncidentTimelineView",
    "INCIDENT_NOT_FOUND",
]
