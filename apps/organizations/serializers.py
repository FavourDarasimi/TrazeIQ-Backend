from rest_framework import serializers

from .models import Invite, Membership, MembershipRole, Organization


class OrganizationInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, min_length=1)


class OrganizationOutputSerializer(serializers.ModelSerializer):
    owner = serializers.UUIDField(source="owner_id", read_only=True)

    class Meta:
        model = Organization
        fields = ["id", "name", "owner", "created_at"]


class MembershipOutputSerializer(serializers.ModelSerializer):
    user = serializers.EmailField(source="user.email", read_only=True)
    user_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Membership
        fields = ["user", "user_id", "role", "created_at"]


# The owner role is never invitable — only the org creator holds it. Admins
# can invite admins, developers and viewers, never a new owner.
INVITABLE_ROLES = [
    (MembershipRole.ADMIN, "admin"),
    (MembershipRole.DEVELOPER, "developer"),
    (MembershipRole.VIEWER, "viewer"),
]


class InviteInputSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=INVITABLE_ROLES, default=MembershipRole.VIEWER
    )


class InviteOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invite
        fields = ["id", "email", "role", "expires_at", "created_at"]


class MembershipAcceptOutputSerializer(serializers.Serializer):
    """The membership created by an accepted invite, with enough org context
    for the frontend to switch into the new workspace."""

    organization = OrganizationOutputSerializer(read_only=True)
    role = serializers.CharField()
