from rest_framework import serializers

from .models import Deployment, Release


class ReleaseOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Release
        fields = [
            "id",
            "project",
            "version",
            "commit_sha",
            "commit_message",
            "commit_author",
            "commit_url",
            "pull_request_url",
            "pull_request_title",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class DeploymentOutputSerializer(serializers.ModelSerializer):
    release = ReleaseOutputSerializer(read_only=True)

    class Meta:
        model = Deployment
        fields = [
            "id",
            "project",
            "release",
            "environment",
            "status",
            "deployed_at",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class DeploymentInputSerializer(serializers.Serializer):
    """Input payload for recording a deployment marker (CLI, SDK, CI/CD)."""

    project_id = serializers.UUIDField(required=False)
    release = serializers.CharField(max_length=128)
    environment = serializers.CharField(
        required=False, allow_blank=True, max_length=32, default="production"
    )
    status = serializers.ChoiceField(
        choices=Deployment.Status.choices,
        required=False,
        default=Deployment.Status.COMPLETED,
    )
    commit_sha = serializers.CharField(
        required=False, allow_blank=True, max_length=64, default=""
    )
    commit_message = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    commit_author = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default=""
    )
    commit_url = serializers.URLField(
        required=False, allow_blank=True, default=""
    )
    pull_request_url = serializers.URLField(
        required=False, allow_blank=True, default=""
    )
    pull_request_title = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default=""
    )
    deployed_at = serializers.DateTimeField(required=False)
    metadata = serializers.JSONField(required=False, default=dict)
    auto_resolve = serializers.BooleanField(required=False, default=False)
    verified_incidents = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
