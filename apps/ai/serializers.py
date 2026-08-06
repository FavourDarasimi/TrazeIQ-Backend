"""Serializers for the ai app."""

from rest_framework import serializers

from .models import AIAnalysis


class AIAnalysisOutputSerializer(serializers.ModelSerializer):
    """Default read shape for an incident's AI root-cause analysis result."""

    class Meta:
        model = AIAnalysis
        fields = [
            "id",
            "incident_id",
            "status",
            "root_cause",
            "suggested_fix",
            "confidence",
            "model_used",
            "created_at",
        ]
        read_only_fields = fields
