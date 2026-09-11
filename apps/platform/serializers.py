from rest_framework import serializers

from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.projects.models import Project


class PlatformUserSerializer(serializers.ModelSerializer):
    """One account row. Explicit fields only — never password hashes."""

    name = serializers.SerializerMethodField()
    org_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "name",
            "is_active",
            "is_staff",
            "email_verified",
            "auth_provider",
            "date_joined",
            "org_count",
        ]
        read_only_fields = fields

    def get_name(self, obj) -> str:
        return obj.first_name or obj.email.split("@")[0]


class PlatformOrganizationSerializer(serializers.ModelSerializer):
    """One tenant row with denormalized owner + counts."""

    owner_email = serializers.EmailField(source="owner.email", read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    project_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "owner_email",
            "member_count",
            "project_count",
            "created_at",
        ]
        read_only_fields = fields


class PlatformProjectSerializer(serializers.ModelSerializer):
    """One project row. The key *hash* is never serialized — only the
    display prefix (same rule as the tenant-facing project endpoints)."""

    organization = serializers.CharField(
        source="organization.name", read_only=True
    )
    events_24h = serializers.IntegerField(read_only=True)
    open_incidents = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "organization",
            "environment",
            "api_key_prefix",
            "events_per_minute",
            "events_24h",
            "open_incidents",
            "created_at",
        ]
        read_only_fields = fields
