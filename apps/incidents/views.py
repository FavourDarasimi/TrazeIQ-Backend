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
from trazeiq_backend.requests import body_as_dict
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


def _parse_bulk_ids(raw_ids) -> list[UUID]:
    """Validate a bulk ``incident_ids`` list into UUIDs (400 otherwise)."""
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
    return valid_uuids


def _bulk_result(updated, valid_uuids, *, message=None):
    """Shared bulk response: count + per-item breakdown + rows.

    ``updated_ids`` are the incidents actually changed; ``skipped_ids`` are
    requested IDs that don't exist or aren't visible to the caller (echoing
    the caller's own input leaks nothing — unknown and foreign IDs are
    indistinguishable by design).
    """
    seen: set[UUID] = set()
    updated_ids = []
    for incident in updated:
        if incident.id not in seen:
            seen.add(incident.id)
            updated_ids.append(str(incident.id))
    skipped_ids = []
    for uuid in valid_uuids:
        if uuid not in seen:
            seen.add(uuid)
            skipped_ids.append(str(uuid))
    events = latest_events_by_id(updated)
    data = {
        "updated_count": len(updated),
        "updated_ids": updated_ids,
        "skipped_ids": skipped_ids,
        "incidents": IncidentOutputSerializer(
            updated, many=True, context={"events": events}
        ).data,
    }
    if message is not None:
        return api_success(data=data, message=message)
    return api_success(data=data)


def _fan_out_bulk(updated, *, event_name="incident.updated", evaluate_alerts=True):
    """Realtime + alert side-effects for a bulk result.

    Pusher publishes stay per-incident (the realtime contract is per
    incident on its project channel). Alert re-evaluation is skipped when
    the update cannot change rule matching — rules only match severity and
    status, so pure assignment changes never re-evaluate.
    """
    for incident in updated:
        publish_incident_event(incident, event_name=event_name)
        if evaluate_alerts:
            enqueue_alert_evaluation(incident.pk)


def _bulk_audit_target(updated, verb: str, extra: str = "") -> str:
    """Audit target carrying the incident IDs so a bulk action is traceable
    back to exactly which incidents it touched."""
    ids = ", ".join(str(incident.id) for incident in updated)
    return f"Bulk {verb} {len(updated)} incident(s): {ids}{extra}"


def _incident_schema(name: str):
    return inline_serializer(
        name,
        fields={"incident": IncidentOutputSerializer()},
    )


class IncidentListView(APIView):
    """GET /api/incidents/ — org-scoped list with status/severity/project
    filters plus free-text search, newest-activity first."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_list",
        summary="List incidents",
        description=(
            "Incidents from every project in the caller's organizations, "
            "most recently-active first. Filter by status, severity or "
            "project, or free-text search across the error-group title and "
            "event message/service/endpoint."
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
                    "search": serializers.CharField(required=False),
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

        raw_search = query.get("search")
        search = raw_search.strip()[:200] if raw_search else None
        if search == "":
            search = None

        incidents = list_incidents_for_user(
            request.user,
            status=query.get("status"),
            severity=query.get("severity"),
            project_id=project_id,
            search=search,
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
        # alert rules inline (cooldown dedups the repeats). Best-effort.
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
                        "updated_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
                        "skipped_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
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
        valid_uuids = _parse_bulk_ids(body_as_dict(request).get("incident_ids", []))

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return _bulk_result(
                [], valid_uuids, message="No updatable incidents found."
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

        _fan_out_bulk(
            updated,
            evaluate_alerts=any(k in updates for k in ("status", "severity")),
        )

        if updated:
            first_org = updated[0].project.organization
            change_summary = ", ".join(f"{k}={v}" for k, v in updates.items())
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=_bulk_audit_target(
                    updated, "updated", extra=f" ({change_summary})"
                ),
            )

        return _bulk_result(updated, valid_uuids)


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
                        "updated_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
                        "skipped_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
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
        valid_uuids = _parse_bulk_ids(body_as_dict(request).get("incident_ids", []))

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return _bulk_result(
                [], valid_uuids, message="No updatable incidents found."
            )

        updated = bulk_update_incidents(
            incidents, actor=request.user, status=Incident.Status.RESOLVED
        )

        _fan_out_bulk(updated, event_name="incident.resolved")

        if updated:
            first_org = updated[0].project.organization
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=_bulk_audit_target(updated, "resolved"),
            )

        return _bulk_result(updated, valid_uuids)


class IncidentBulkIgnoreView(APIView):
    """POST /api/v1/incidents/bulk-ignore/ — mark multiple incidents ignored.

    The archive counterpart to bulk-resolve: ignored incidents leave the
    active workflow without counting as fixed. Idempotent for already-ignored
    rows (they are returned as updated, like the resolve endpoint does).
    """

    permission_classes = [IsAuthenticated, IsBulkIncidentDeveloperOrAbove]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_bulk_ignore",
        summary="Bulk ignore incidents",
        request=inline_serializer(
            "BulkIgnoreRequest",
            fields={"incident_ids": serializers.ListField(child=serializers.UUIDField())},
        ),
        responses={
            200: envelope_schema(
                "IncidentBulkIgnoreOk",
                payload=inline_serializer(
                    "IncidentBulkIgnoreData",
                    fields={
                        "updated_count": serializers.IntegerField(),
                        "updated_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
                        "skipped_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
                        "incidents": IncidentOutputSerializer(many=True),
                    },
                ),
            ),
            400: envelope_schema("IncidentBulkIgnoreValidation", error=True),
            401: envelope_schema("IncidentBulkIgnoreUnauthorized", error=True),
            403: envelope_schema("IncidentBulkIgnoreForbidden", error=True),
        },
    )
    def post(self, request):
        valid_uuids = _parse_bulk_ids(body_as_dict(request).get("incident_ids", []))

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return _bulk_result(
                [], valid_uuids, message="No updatable incidents found."
            )

        updated = bulk_update_incidents(
            incidents, actor=request.user, status=Incident.Status.IGNORED
        )

        _fan_out_bulk(updated)

        if updated:
            first_org = updated[0].project.organization
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=_bulk_audit_target(updated, "ignored"),
            )

        return _bulk_result(updated, valid_uuids)


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
                        "updated_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
                        "skipped_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
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
        data = body_as_dict(request)
        raw_ids = data.get("incident_ids", [])
        valid_uuids = _parse_bulk_ids(raw_ids)

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return _bulk_result(
                [], valid_uuids, message="No updatable incidents found."
            )

        org_ids = {inc.project.organization_id for inc in incidents}
        serializer = BulkUpdateSerializer(
            data={"incident_ids": raw_ids, "assigned_to": data.get("assigned_to")},
            context={"organization_ids": org_ids},
        )
        serializer.is_valid(raise_exception=True)

        updated = bulk_update_incidents(
            incidents,
            actor=request.user,
            assigned_to=serializer.validated_data.get("assigned_to"),
        )

        # Assignment changes cannot match alert rules (severity/status only),
        # so alert re-evaluation is skipped here.
        _fan_out_bulk(updated, evaluate_alerts=False)

        if updated:
            first_org = updated[0].project.organization
            assignee = serializer.validated_data.get("assigned_to")
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=_bulk_audit_target(
                    updated,
                    "assigned",
                    extra=f" to {assignee.email if assignee else 'unassigned'}",
                ),
            )

        return _bulk_result(updated, valid_uuids)


class IncidentBulkView(APIView):
    """POST /api/v1/incidents/bulk/ — generic bulk endpoint for backwards compat.

    Accepts the older `{"ids": [...], "action": "resolve"}` shape from Issue #5
    as well as the newer `{"incident_ids": [...], "status": ...}` shape.
    `action: resolve` maps to `status: resolved`. Any `status`/`severity`/
    `assigned_to` fields are forwarded to the bulk service. This keeps the
    floating BulkActionBar working even if the UI was built against the generic
    URL, while the specific `bulk-resolve`/`bulk-update`/`bulk-assign`/
    `bulk-ignore` routes remain the canonical ones.
    """

    permission_classes = [IsAuthenticated, IsBulkIncidentDeveloperOrAbove]

    @extend_schema(
        tags=["incidents"],
        operation_id="incidents_bulk",
        summary="Bulk incident action (generic)",
        description=(
            "Generic bulk endpoint. Provide `ids` or `incident_ids` and either "
            "`action: resolve|open|investigating|ignored` or explicit "
            "`status`/`severity`/`assigned_to` fields."
        ),
        request=inline_serializer(
            "BulkGenericRequest",
            fields={
                "ids": serializers.ListField(
                    child=serializers.UUIDField(), required=False
                ),
                "incident_ids": serializers.ListField(
                    child=serializers.UUIDField(), required=False
                ),
                "action": serializers.CharField(required=False),
                "status": serializers.ChoiceField(
                    choices=Incident.Status.choices, required=False
                ),
                "severity": serializers.ChoiceField(
                    choices=Incident.Severity.choices, required=False
                ),
                "assigned_to": serializers.UUIDField(
                    required=False, allow_null=True
                ),
            },
        ),
        responses={
            200: envelope_schema(
                "IncidentBulkOk",
                payload=inline_serializer(
                    "IncidentBulkData",
                    fields={
                        "updated_count": serializers.IntegerField(),
                        "updated_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
                        "skipped_ids": serializers.ListField(
                            child=serializers.UUIDField()
                        ),
                        "incidents": IncidentOutputSerializer(many=True),
                    },
                ),
            ),
            400: envelope_schema("IncidentBulkValidation", error=True),
            401: envelope_schema("IncidentBulkUnauthorized", error=True),
            403: envelope_schema("IncidentBulkForbidden", error=True),
        },
    )
    def post(self, request):
        data = body_as_dict(request)
        raw_ids = data.get("incident_ids")
        if raw_ids is None:
            raw_ids = data.get("ids", [])
        valid_uuids = _parse_bulk_ids(raw_ids)

        incidents = list(get_updatable_incidents_for_user(valid_uuids, request.user))
        if not incidents:
            return _bulk_result(
                [], valid_uuids, message="No updatable incidents found."
            )

        # Normalize action -> status
        action = data.get("action")
        status_val = data.get("status")
        if action and not status_val:
            action_map = {
                "resolve": Incident.Status.RESOLVED,
                "resolved": Incident.Status.RESOLVED,
                "open": Incident.Status.OPEN,
                "investigating": Incident.Status.INVESTIGATING,
                "ignored": Incident.Status.IGNORED,
            }
            status_val = action_map.get(str(action).lower())
            if status_val is None:
                raise serializers.ValidationError(
                    {"action": f"Unknown action: {action}"}
                )

        org_ids = {inc.project.organization_id for inc in incidents}
        payload: dict = {"incident_ids": raw_ids}
        if status_val is not None:
            payload["status"] = status_val
        if "severity" in data:
            payload["severity"] = data["severity"]
        if "assigned_to" in data:
            payload["assigned_to"] = data["assigned_to"]

        if not any(k in payload for k in ("status", "severity", "assigned_to")):
            raise serializers.ValidationError(
                "At least one of status, severity, assigned_to, or action is required."
            )

        serializer = BulkUpdateSerializer(
            data=payload, context={"organization_ids": org_ids}
        )
        serializer.is_valid(raise_exception=True)

        updates = {
            k: serializer.validated_data[k]
            for k in ("status", "severity", "assigned_to")
            if k in serializer.validated_data
        }
        updated = bulk_update_incidents(incidents, actor=request.user, **updates)

        _fan_out_bulk(
            updated,
            event_name=(
                "incident.resolved"
                if updates.get("status") == Incident.Status.RESOLVED
                else "incident.updated"
            ),
            evaluate_alerts=any(k in updates for k in ("status", "severity")),
        )

        if updated:
            first_org = updated[0].project.organization
            change_summary = ", ".join(f"{k}={v}" for k, v in updates.items())
            record_audit_log(
                actor=request.user,
                organization=first_org,
                action=AuditAction.INCIDENTS_BULK_UPDATED,
                target=_bulk_audit_target(
                    updated, "updated", extra=f" ({change_summary})"
                ),
            )

        return _bulk_result(updated, valid_uuids)


__all__ = [
    "IncidentBulkAssignView",
    "IncidentBulkIgnoreView",
    "IncidentBulkResolveView",
    "IncidentBulkUpdateView",
    "IncidentCommentView",
    "IncidentDetailView",
    "IncidentListView",
    "IncidentResolveView",
    "IncidentTimelineView",
    "INCIDENT_NOT_FOUND",
]
