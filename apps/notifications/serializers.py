"""Serializers for the notification inbox and alert preferences.

Read shapes only — inbox rows are created by the delivery services, never
by POST bodies; the only write inputs are a mark-read id list and the
preference knobs.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import AlertPreference, Notification


class _NotificationIncidentSummary(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    severity = serializers.CharField()
    status = serializers.CharField()


class NotificationOutputSerializer(serializers.ModelSerializer):
    """Read shape for one inbox row — the incident is a nested summary so
    the client can deep-link without a second request."""

    incident = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "incident",
            "kind",
            "title",
            "body",
            "is_read",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(_NotificationIncidentSummary(allow_null=True))
    def get_incident(self, notification):
        incident = notification.incident
        if incident is None:
            return None
        return {
            "id": str(incident.id),
            "title": incident.error_group.title,
            "severity": incident.severity,
            "status": incident.status,
        }


class NotificationMarkReadInputSerializer(serializers.Serializer):
    """POST body for the mark-read endpoint. Empty/missing ``ids`` means
    "mark everything read"."""

    ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )


class AlertPreferenceOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertPreference
        fields = [
            "only_assigned_to_me",
            "notify_on_new_incidents",
            "notify_on_status_changes",
            "notify_on_comments",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class AlertPreferenceInputSerializer(serializers.ModelSerializer):
    """PATCH body for the preference knobs — each stays optional so the
    client can flip a single switch."""

    class Meta:
        model = AlertPreference
        fields = [
            "only_assigned_to_me",
            "notify_on_new_incidents",
            "notify_on_status_changes",
            "notify_on_comments",
        ]