"""Phase 4C: alert rules and dispatch logs.

An ``AlertRule`` watches one project for matching incidents (``condition``
is a small JSON dict, e.g. ``{"severity": "critical"}``); the evaluation
task logs one ``AlertLog`` per (rule, incident) dispatch, with the rule's
``cooldown_minutes`` window suppressing repeats. Actual channel delivery
(email / Slack / webhook) lands in Phase 4D — the log row *is* the dispatch
attempt here.
"""

from django.db import models

from trazeiq_backend.models import UUIDModel

from apps.incidents.models import Incident


class AlertRule(UUIDModel):
    """When should an incident in this project trigger a dispatch?

    ``condition`` keys are limited to the incident surface the engine can
    match on — ``severity`` and ``status`` — validated at serialization so
    an untyped JSON blob can never silently match everything.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SLACK = "slack", "Slack"
        WEBHOOK = "webhook", "Webhook"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="alert_rules",
    )
    name = models.CharField(max_length=120)
    condition = models.JSONField(default=dict)
    channel = models.CharField(max_length=16, choices=Channel.choices)
    target = models.CharField(max_length=500)
    cooldown_minutes = models.PositiveIntegerField(default=15)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["project"],
                name="idx_alertrule_project",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.project_id})"


class AlertLog(UUIDModel):
    """One dispatch attempt for a (rule, incident) pair.

    Only real dispatches are recorded — evaluations suppressed by the
    cooldown window leave no trace, so the log stays a faithful "alerts
    actually sent" record.
    """

    rule = models.ForeignKey(
        AlertRule,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    incident = models.ForeignKey(
        Incident,
        on_delete=models.CASCADE,
        related_name="alert_logs",
    )
    dispatched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-dispatched_at"]
        indexes = [
            models.Index(
                fields=["rule", "incident", "dispatched_at"],
                name="idx_alertlog_rule_incident",
            ),
        ]

    def __str__(self):
        return f"alert {self.rule.name} -> {self.incident_id}"