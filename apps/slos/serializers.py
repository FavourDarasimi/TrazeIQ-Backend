"""Serializers for SLOs, budget snapshots, and breach alerts.

The input serializer validates ``error_query`` to a dict over the
``severity``/``service`` keys the engine actually consumes — a mis-typed
condition fails loudly at write time, never silently matches nothing.
"""

from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.events.models import Event
from apps.incidents.models import Incident

from .models import SLO, SLOBreachAlert, SLOBudgetSnapshot

SUPPORTED_QUERY_KEYS = ("severity", "service")
_VALID_LEVELS = {choice[0] for choice in Event.Level.choices}
_VALID_SEVERITIES = {choice[0] for choice in Incident.Severity.choices}


class SLOBudgetSnapshotSerializer(serializers.ModelSerializer):
    """Read shape for a budget snapshot."""

    evaluated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SLOBudgetSnapshot
        fields = [
            "id",
            "period_start",
            "period_end",
            "total_events",
            "bad_events",
            "success_rate",
            "budget_total",
            "budget_remaining_pct",
            "burn_rate_1h",
            "burn_rate_6h",
            "burn_rate_24h",
            "burn_rate_72h",
            "status",
            "evaluated_at",
        ]
        read_only_fields = fields


class SLOBreachAlertSerializer(serializers.ModelSerializer):
    """Read shape for a breach alert — includes the owning SLO's id and
    the actor email so the dashboard can route acknowledge actions and
    render "Acknowledged by ..." inline."""

    acknowledged_by_email = serializers.SerializerMethodField()

    class Meta:
        model = SLOBreachAlert
        fields = [
            "id",
            "slo",
            "kind",
            "budget_remaining_pct",
            "message",
            "fired_at",
            "acknowledged_at",
            "acknowledged_by_email",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_acknowledged_by_email(self, alert):
        return alert.acknowledged_by.email if alert.acknowledged_by else None


class SLOInputSerializer(serializers.ModelSerializer):
    """Write shape for an SLO. ``project`` is set from the URL/body
    context, not by the client directly except on create."""

    project = serializers.PrimaryKeyRelatedField(read_only=True)
    target_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100,
    )
    alert_threshold_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False
    )
    window_days = serializers.IntegerField(min_value=1, max_value=365, required=False)
    name = serializers.CharField(max_length=120)

    class Meta:
        model = SLO
        fields = [
            "project",
            "name",
            "description",
            "target_pct",
            "window_days",
            "error_query",
            "alert_threshold_pct",
            "enabled",
        ]

    def validate_target_pct(self, value):
        v = Decimal(str(value))
        if v <= 0 or v >= 100:
            raise serializers.ValidationError(
                "Target must be strictly between 0 and 100."
            )
        return value

    def validate_error_query(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "error_query must be an object, e.g. "
                '{"severity": "critical", "service": "api"}.'
            )
        unknown = set(value) - set(SUPPORTED_QUERY_KEYS)
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported error_query keys: {', '.join(sorted(unknown))}. "
                f"Supported: {', '.join(SUPPORTED_QUERY_KEYS)}."
            )
        if "severity" in value and value["severity"] not in _VALID_SEVERITIES:
            raise serializers.ValidationError(
                f"severity must be one of: {', '.join(sorted(_VALID_SEVERITIES))}."
            )
        if "service" in value and not isinstance(value["service"], str):
            raise serializers.ValidationError(
                "service must be a string."
            )
        return value

    def validate_alert_threshold_pct(self, value):
        v = Decimal(str(value))
        if v < 0 or v > 100:
            raise serializers.ValidationError(
                "alert_threshold_pct must be between 0 and 100."
            )
        return value


class SLOOutputSerializer(serializers.ModelSerializer):
    """Read shape for an SLO — plus the latest snapshot's headline so the
    dashboard's list view can render without a second round-trip."""

    project = serializers.SerializerMethodField()
    latest_snapshot = serializers.SerializerMethodField()

    class Meta:
        model = SLO
        fields = [
            "id",
            "project",
            "name",
            "description",
            "target_pct",
            "window_days",
            "error_query",
            "alert_threshold_pct",
            "enabled",
            "created_at",
            "updated_at",
            "latest_snapshot",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_project(self, slo):
        return {"id": str(slo.project_id), "name": slo.project.name}

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_latest_snapshot(self, slo):
        latest = (
            SLOBudgetSnapshot.objects.filter(slo=slo)
            .order_by("-evaluated_at")
            .first()
        )
        if not latest:
            return None
        return SLOBudgetSnapshotSerializer(latest).data


# Inline serializer for the project-impact view.
class SLODependencyRowSerializer(serializers.Serializer):
    slo_id = serializers.UUIDField()
    name = serializers.CharField()
    service = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    budget_remaining_pct = serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True
    )
    burn_rate_1h = serializers.DecimalField(max_digits=12, decimal_places=4)
    burn_rate_24h = serializers.DecimalField(max_digits=12, decimal_places=4)


class SLODependencySummarySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    evaluated_at = serializers.DateTimeField()
    total_slos = serializers.IntegerField()
    breached_slos = serializers.IntegerField()
    exhausted_slos = serializers.IntegerField()
    slos = SLODependencyRowSerializer(many=True)