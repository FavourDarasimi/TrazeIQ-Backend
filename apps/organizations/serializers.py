from rest_framework import serializers

from .models import Membership, Organization


class OrganizationInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, min_length=1)


class OrganizationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "owner", "created_at"]


class MembershipOutputSerializer(serializers.ModelSerializer):
    user = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = Membership
        fields = ["user", "role", "created_at"]
