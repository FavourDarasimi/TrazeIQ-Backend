from rest_framework import serializers

from .models import Membership, Organization


class OrganizationInSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, min_length=1)


class OrganizationOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "owner", "created_at"]


class MembershipOutSerializer(serializers.ModelSerializer):
    user = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = Membership
        fields = ["user", "role", "created_at"]
