from django.db import models


class AIAnalysis(models.Model):
    """The result of one LLM root-cause run for an incident.

    One row per analysis attempt, not per event — the ingestion path only
    enqueues a task for a brand-new incident or when the last analysis is
    stale (``AI_ANALYSIS_CACHE_HOURS``), which is what keeps us inside
    OpenRouter's free-tier rate limits (spec §7).

    ``status`` tracks the task lifecycle so the API (Phase 2C) can report
    pending/ready/failed without guessing: the row is created as ``pending``
    when the task starts, then flips to ``ready`` or ``failed``. The partial
    unique constraint allows at most one in-flight row per incident, so a
    duplicated task enqueue can't double-spend model calls.

    ``raw_response`` keeps the model's raw attempts (one entry per call:
    model + content) for debugging.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Confidence(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    incident = models.ForeignKey(
        "incidents.Incident",
        on_delete=models.CASCADE,
        related_name="analyses",
    )
    root_cause = models.TextField(blank=True, default="")
    suggested_fix = models.TextField(blank=True, default="")
    confidence = models.CharField(
        max_length=16, choices=Confidence.choices, blank=True, default=""
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    model_used = models.CharField(max_length=128, blank=True, default="")
    raw_response = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["incident", "-created_at"],
                name="idx_ai_analysis_incident",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["incident"],
                condition=models.Q(status="pending"),
                name="uniq_ai_analysis_pending_incident",
            ),
        ]

    def __str__(self):
        return f"AIAnalysis #{self.pk} {self.status} ({self.model_used})"
