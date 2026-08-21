from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from django.contrib.auth import get_user_model

from apps.events.models import Event, ErrorGroup
from apps.projects.models import Project

from .models import Incident, TimelineEntry

User = get_user_model()


class ProjectSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "name", "environment"]
        read_only_fields = fields


class ErrorGroupSummarySerializer(serializers.ModelSerializer):
    """The deduped signature behind an incident — title, counts, window."""

    class Meta:
        model = ErrorGroup
        fields = [
            "id",
            "fingerprint",
            "title",
            "count",
            "first_seen",
            "last_seen",
        ]
        read_only_fields = fields


class EventSummarySerializer(serializers.ModelSerializer):
    """The most recent raw occurrence of an incident's error group."""

    class Meta:
        model = Event
        fields = [
            "id",
            "message",
            "stacktrace",
            "level",
            "environment",
            "service",
            "endpoint",
            "created_at",
        ]
        read_only_fields = fields


class IncidentOutputSerializer(serializers.ModelSerializer):
    """Read shape for incidents (list + detail) — Phase 1F static views."""

    project = ProjectSummarySerializer(read_only=True)
    error_group = ErrorGroupSummarySerializer(read_only=True)
    latest_event = serializers.SerializerMethodField()
    assigned_to = serializers.UUIDField(source="assigned_to_id", read_only=True)
    assigned_to_email = serializers.EmailField(
        source="assigned_to.email", read_only=True, allow_null=True
    )

    @extend_schema_field(EventSummarySerializer(allow_null=True))
    def get_latest_event(self, incident):
        """Resolve the annotated ``latest_event_id`` from the context map the
        view built with :func:`apps.incidents.selectors.latest_events_by_id`.
        Falls back to ``None`` when the group has no occurrences yet."""
        latest_event_id = getattr(incident, "latest_event_id", None)
        if latest_event_id is None:
            return None
        event = (self.context.get("events") or {}).get(latest_event_id)
        if event is None:
            return None
        return EventSummarySerializer(event).data

    class Meta:
        model = Incident
        fields = [
            "id",
            "project",
            "error_group",
            "severity",
            "status",
            "assigned_to",
            "assigned_to_email",
            "created_at",
            "resolved_at",
            "latest_event",
        ]
        read_only_fields = fields


class IncidentUpdateSerializer(serializers.Serializer):
    """PATCH /api/incidents/{id}/ — status/severity/assignment (Phase 4A).

    ``assigned_to`` only accepts users who are members of the incident's
    organization — you can't assign work to an outsider.
    """

    status = serializers.ChoiceField(
        choices=Incident.Status.choices, required=False
    )
    severity = serializers.ChoiceField(
        choices=Incident.Severity.choices, required=False
    )
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=False,
        allow_null=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization_id = self.context.get("organization_id")
        if organization_id is not None:
            self.fields["assigned_to"].queryset = User.objects.filter(
                memberships__organization_id=organization_id
            )


class TimelineEntryOutputSerializer(serializers.Serializer):
    """One row of the incident timeline feed — Phase 4B mixes all four kinds.

    Rows come pre-shaped from :func:`apps.incidents.selectors.list_incident_timeline`
    as uniform dicts: ``event`` rows carry level/message/environment/service
    (``content``/``actor_email`` empty), the other kinds carry content and an
    optional actor email. Defaults keep the shape constant across kinds.
    """

    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=TimelineEntry.Kind.choices)
    level = serializers.CharField(required=False, allow_blank=True, default="")
    message = serializers.CharField(required=False, allow_blank=True, default="")
    environment = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    service = serializers.CharField(required=False, allow_blank=True, default="")
    content = serializers.CharField(required=False, allow_blank=True, default="")
    actor_email = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    @extend_schema_field(serializers.EmailField(allow_null=True))
    def get_actor_email(self, obj):
        """Timeline rows arrive as pre-shaped dicts; the comment POST
        response serializes a live model. Handle both."""
        if isinstance(obj, dict):
            return obj.get("actor_email")
        return obj.actor.email if getattr(obj, "actor", None) else None


class CommentInputSerializer(serializers.Serializer):
    """POST /api/incidents/{id}/comments/ body — just the comment text."""

    content = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=5000,
    )


class BulkUpdateSerializer(serializers.Serializer):
    """POST /api/v1/incidents/bulk-update/ — apply the same status/severity/assignment
    change to multiple incidents at once.

    ``incident_ids`` accepts 1–100 UUIDs. At least one update field (status,
    severity, or assigned_to) must be provided.
    """

    incident_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=100,
    )
    status = serializers.ChoiceField(
        choices=Incident.Status.choices, required=False
    )
    severity = serializers.ChoiceField(
        choices=Incident.Severity.choices, required=False
    )
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=False,
        allow_null=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization_ids = self.context.get("organization_ids")
        organization_id = self.context.get("organization_id")
        if organization_ids:
            self.fields["assigned_to"].queryset = User.objects.filter(
                memberships__organization_id__in=organization_ids
            ).distinct()
        elif organization_id is not None:
            self.fields["assigned_to"].queryset = User.objects.filter(
                memberships__organization_id=organization_id
            ).distinct()

    def validate(self, attrs):
        has_update = any(
            key in attrs for key in ("status", "severity", "assigned_to")
        )
        if not has_update:
            raise serializers.ValidationError(
                "At least one of status, severity, or assigned_to is required."
            )
        return attrs
