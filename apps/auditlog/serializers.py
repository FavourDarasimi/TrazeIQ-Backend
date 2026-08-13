from rest_framework import serializers

from .models import AuditLog


class AuditLogOutputSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "organization",
            "actor",
            "actor_email",
            "action",
            "target",
            "created_at",
        ]
        read_only_fields = fields
