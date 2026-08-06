from django.conf import settings
from django.db import models


class Incident(models.Model):
    """The tracked workflow around an ErrorGroup.

    Distinct from the error group on purpose: a group is a fact about the
    codebase ("this error keeps happening"), an incident is the ticket being
    worked. An incident can be reopened against the same group later.

    ``project`` is denormalized from ``error_group.project`` — it never
    changes in practice, and it buys the hot-path ``(project_id, status)``
    index plus clean tenant scoping (Agent.md rule 2).
    """

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED = "resolved", "Resolved"
        IGNORED = "ignored", "Ignored"

    error_group = models.ForeignKey(
        "events.ErrorGroup",
        on_delete=models.CASCADE,
        related_name="incidents",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="incidents",
    )
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.MEDIUM
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_incidents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["project", "status"],
                name="idx_incident_project_status",
            ),
        ]

    def __str__(self):
        return f"#{self.pk} {self.severity}/{self.status} {self.error_group}"


class TimelineEntry(models.Model):
    """One row of an incident's history: event occurrence, comment, status
    change, or AI analysis (spec data model).

    Phase 2B writes ``ai_analysis`` entries when an analysis completes; the
    timeline endpoint switches onto this model and the comment/status-change
    writers arrive in Phase 4B.
    """

    class Kind(models.TextChoices):
        EVENT = "event", "Event"
        COMMENT = "comment", "Comment"
        STATUS_CHANGE = "status_change", "Status change"
        AI_ANALYSIS = "ai_analysis", "AI analysis"

    incident = models.ForeignKey(
        Incident,
        on_delete=models.CASCADE,
        related_name="timeline_entries",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    content = models.TextField(blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="timeline_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["incident", "created_at"],
                name="idx_timeline_incident_created",
            ),
        ]

    def __str__(self):
        return f"{self.kind} on incident #{self.incident_id}"