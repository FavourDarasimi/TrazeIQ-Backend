"""SLO definitions, error-budget snapshots, and breach alerts.

An SLO is a statement like "99.9% of API requests succeed over a rolling
30-day window". We track the ``error_query`` (currently ``severity`` +
optional ``service`` filter), and materialize a :class:`SLOBudgetSnapshot`
on each evaluation pass — total events, bad events, remaining budget, and
multi-window burn rates (1h / 6h / 24h / 72h) per the Google SRE workbook's
multi-window burn-rate alert guidance.

``SLOBreachAlert`` records the moment an SLO crosses the
``alert_threshold_pct`` (default 30% of budget remaining), one row per kind
per active period so a long-running breach doesn't fire on every evaluation.
"""

from django.conf import settings
from django.db import models

from trazeiq_backend.models import UUIDModel


class SLO(UUIDModel):
    """A service-level objective scoped to one project.

    ``error_query`` is a small, validated dict the budget evaluator can
    match on: ``{"severity": "critical|high|medium|low", "service": "..."}``.
    A missing ``service`` means the SLO watches every service the project
    sees; a missing ``severity`` means errors at any level count. The
    serializer enforces this contract so a typo can never silently match
    nothing.
    """

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="slos",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    target_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="The success-rate target (e.g. 99.90 means 99.9% uptime).",
    )
    window_days = models.PositiveIntegerField(
        default=30,
        help_text="Rolling window the error budget is measured over.",
    )
    error_query = models.JSONField(
        default=dict,
        help_text=(
            "Filter applied to events when counting bad events: "
            "{severity?, service?}."
        ),
    )
    alert_threshold_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=25,
        help_text=(
            "Percentage of budget remaining below which a breach alert is "
            "fired (e.g. 25 means fire when 75% of the budget is consumed)."
        ),
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project"], name="idx_slo_project"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.target_pct}% over {self.window_days}d)"


class SLOBudgetSnapshot(UUIDModel):
    """One evaluation pass for one SLO.

    Carries the headline numbers (total / bad events, remaining budget) and
    the multi-window burn rates used by the dashboard chart. The status
    field is derived from the burn rates: ``exhausted`` when no budget
    remains, ``critical`` when 1h/6h burn would empty the budget inside the
    window, ``warning`` when only 24h/72h burn would empty it, else
    ``healthy``.
    """

    class Status(models.TextChoices):
        HEALTHY = "healthy", "Healthy"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"
        EXHAUSTED = "exhausted", "Exhausted"

    slo = models.ForeignKey(
        SLO,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    total_events = models.PositiveIntegerField(default=0)
    bad_events = models.PositiveIntegerField(default=0)
    success_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=0,
        help_text="(1 - bad/total) as a fraction; 0 when total=0.",
    )
    budget_total = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        help_text="Allowed bad events in the window: total * (1 - target).",
    )
    budget_remaining_pct = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=0,
        help_text="0-100; 100 = full budget remaining, 0 = exhausted.",
    )
    burn_rate_1h = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        help_text="Bad-event rate per hour over the last hour.",
    )
    burn_rate_6h = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        help_text="Bad-event rate per hour over the last 6 hours.",
    )
    burn_rate_24h = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        help_text="Bad-event rate per hour over the last 24 hours.",
    )
    burn_rate_72h = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        help_text="Bad-event rate per hour over the last 72 hours.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.HEALTHY,
    )
    evaluated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-evaluated_at"]
        indexes = [
            models.Index(
                fields=["slo", "-evaluated_at"],
                name="idx_slosnap_slo_evaluated",
            ),
        ]

    def __str__(self) -> str:
        return f"snapshot {self.slo_id} @ {self.evaluated_at:%Y-%m-%d %H:%M}"


class SLOBreachAlert(UUIDModel):
    """A fired breach alert — one row per (slo, kind) per active period.

    Dedupe is by ``kind``: a long-running critical burn produces a single
    ``critical`` row, refreshed on each evaluation that still finds the
    breach active. A new row is only created when the breach resets and the
    next threshold crossing fires.
    """

    class Kind(models.TextChoices):
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"
        EXHAUSTED = "exhausted", "Exhausted"

    slo = models.ForeignKey(
        SLO,
        on_delete=models.CASCADE,
        related_name="breach_alerts",
    )
    snapshot = models.ForeignKey(
        SLOBudgetSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="breach_alerts",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    budget_remaining_pct = models.DecimalField(
        max_digits=7, decimal_places=2, default=0
    )
    message = models.CharField(max_length=255)
    fired_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slo_breach_alerts",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-fired_at"]
        indexes = [
            models.Index(
                fields=["slo", "kind", "fired_at"],
                name="idx_sloalert_slo_kind",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} alert for SLO {self.slo_id} @ {self.fired_at:%Y-%m-%d %H:%M}"