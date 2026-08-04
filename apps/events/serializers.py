from rest_framework import serializers

from .models import Event
from .validators import validate_payload_size


class EventInputSerializer(serializers.Serializer):
    """Ingestion payload — what the monitored app POSTs to `/api/v1/events/`."""

    message = serializers.CharField()
    stacktrace = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    level = serializers.ChoiceField(
        choices=Event.Level.choices, default=Event.Level.ERROR
    )
    environment = serializers.CharField(
        required=False, allow_blank=True, max_length=32, default=""
    )
    service = serializers.CharField(
        required=False, allow_blank=True, max_length=64, default=""
    )
    endpoint = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default=""
    )
    request_method = serializers.CharField(
        required=False, allow_blank=True, max_length=16, default=""
    )
    user_id = serializers.CharField(
        required=False, allow_blank=True, max_length=128, default=""
    )
    ip_address = serializers.CharField(
        required=False, allow_blank=True, max_length=64, default=""
    )
    metadata = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        validate_payload_size(attrs["message"], attrs.get("stacktrace", ""))
        return attrs


class EventOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            "id",
            "project",
            "error_group",
            "message",
            "stacktrace",
            "level",
            "environment",
            "service",
            "endpoint",
            "request_method",
            "user_id",
            "ip_address",
            "metadata",
            "fingerprint",
            "created_at",
        ]
        read_only_fields = fields