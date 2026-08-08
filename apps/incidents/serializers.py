from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.events.models import Event, ErrorGroup
from apps.projects.models import Project

from .models import Incident


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
            "created_at",
            "resolved_at",
            "latest_event",
        ]
        read_only_fields = fields


class TimelineEntryOutputSerializer(serializers.Serializer):
    """One row of the incident timeline feed.

    Phase 1F serves the static occurrence feed (``kind="event"`` rows only).
    Later phases extend ``kind`` with ``comment`` / ``status_change`` /
    ``ai_analysis`` — the shape below is their contract.
    """

    id = serializers.UUIDField()
    kind = serializers.CharField()
    level = serializers.CharField()
    message = serializers.CharField()
    environment = serializers.CharField()
    service = serializers.CharField()
    created_at = serializers.DateTimeField()
