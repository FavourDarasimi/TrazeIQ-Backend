from rest_framework import serializers

from .models import Project


class ProjectInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, min_length=1)
    organization = serializers.UUIDField(required=False)
    environment = serializers.CharField(
        max_length=32, required=False, default="production"
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
            "created_at",
        ]
        read_only_fields = ["id", "organization", "api_key_prefix", "created_at"]