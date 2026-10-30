from rest_framework import serializers

from .models import ApiKeyScope, ApiKeyStatus, Project, ProjectApiKey


class ProjectInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, min_length=1)
    organization = serializers.UUIDField(required=False)
    environment = serializers.CharField(
        max_length=32, required=False, default="production"
    )
    events_per_minute = serializers.IntegerField(
        min_value=1,
        max_value=1_000_000,
        required=False,
        help_text="Per-project ingestion cap (events/minute).",
    )


class ProjectOutputSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "organization",
            "name",
            "api_key_prefix",
            "environment",
            "events_per_minute",
            "created_at",
        ]
        read_only_fields = ["id", "organization", "api_key_prefix", "created_at"]


class ProjectApiKeyInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, min_length=1)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=ApiKeyScope.choices),
        required=False,
        default=[ApiKeyScope.INGEST_ONLY],
    )


class ProjectApiKeyRotateInputSerializer(serializers.Serializer):
    grace_period_hours = serializers.IntegerField(
        min_value=0,
        max_value=720,
        required=False,
        default=24,
        help_text="Grace period window (hours) during which the previous key remains valid. 0 = revoke immediately.",
    )
    name = serializers.CharField(
        max_length=120, required=False, allow_blank=True
    )


class ProjectApiKeyOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectApiKey
        fields = [
            "id",
            "name",
            "api_key_prefix",
            "scopes",
            "status",
            "expires_at",
            "revoked_at",
            "last_used_at",
            "request_count",
            "created_at",
        ]
        read_only_fields = fields
