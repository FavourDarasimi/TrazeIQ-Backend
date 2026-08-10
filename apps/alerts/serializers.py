"""Phase 4C: serializers for alert rules and logs.

The engine can only match what it understands — ``condition`` is validated
to a dict over ``severity`` / ``status`` with valid choice values, so a
mis-typed rule fails loudly at creation instead of silently never firing.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.incidents.models import Incident
from apps.projects.models import Project

from .models import AlertLog, AlertRule

SUPPORTED_CONDITION_KEYS = ("severity", "status")

_CONDITION_CHOICES = {
    "severity": {choice[0] for choice in Incident.Severity.choices},
    "status": {choice[0] for choice in Incident.Status.choices},
}


class _RuleProjectSummary(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class _LogRuleSummary(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    channel = serializers.CharField()
    target = serializers.CharField()


class _LogIncidentSummary(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    severity = serializers.CharField()
    status = serializers.CharField()


class AlertRuleInputSerializer(serializers.ModelSerializer):
    """POST/PATCH body for an alert rule.

    ``project`` is only writable on create; PATCH moves are rejected so a
    rule can never be silently transplanted into another project.
    """

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.none(),
        write_only=True,
        required=False,
    )

    class Meta:
        model = AlertRule
        fields = [
            "project",
            "name",
            "condition",
            "channel",
            "target",
            "cooldown_minutes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization_id = self.context.get("organization_id")
        if organization_id is not None:
            self.fields["project"].queryset = Project.objects.filter(
                organization_id=organization_id
            )

    def validate_condition(self, value):
        if not isinstance(value, dict) or not value:
            raise serializers.ValidationError(
                "Condition must be a non-empty object, e.g. "
                '{"severity": "critical"}'
            )
        invalid_keys = set(value) - set(SUPPORTED_CONDITION_KEYS)
        if invalid_keys:
            raise serializers.ValidationError(
                f"Unsupported condition keys: {', '.join(sorted(invalid_keys))}. "
                f"Supported: {', '.join(SUPPORTED_CONDITION_KEYS)}"
            )
        for key, choice in value.items():
            allowed = _CONDITION_CHOICES[key]
            if choice not in allowed:
                raise serializers.ValidationError(
                    f"{key} must be one of: {', '.join(sorted(allowed))}"
                )
        return value


class AlertRuleOutputSerializer(serializers.ModelSerializer):
    """Read shape for rules — the rule plus its project name."""

    project = serializers.SerializerMethodField()

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "project",
            "name",
            "condition",
            "channel",
            "target",
            "cooldown_minutes",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(_RuleProjectSummary())
    def get_project(self, rule):
        return {
            "id": str(rule.project.id),
            "name": rule.project.name,
        }


class AlertLogOutputSerializer(serializers.ModelSerializer):
    """Read shape for dispatch logs — rule and incident summaries."""

    rule = serializers.SerializerMethodField()
    incident = serializers.SerializerMethodField()

    class Meta:
        model = AlertLog
        fields = [
            "id",
            "rule",
            "incident",
            "dispatched_at",
        ]
        read_only_fields = fields

    @extend_schema_field(_LogRuleSummary())
    def get_rule(self, log):
        return {
            "id": str(log.rule.id),
            "name": log.rule.name,
            "channel": log.rule.channel,
            "target": log.rule.target,
        }

    @extend_schema_field(_LogIncidentSummary())
    def get_incident(self, log):
        return {
            "id": str(log.incident.id),
            "title": log.incident.error_group.title,
            "severity": log.incident.severity,
            "status": log.incident.status,
        }