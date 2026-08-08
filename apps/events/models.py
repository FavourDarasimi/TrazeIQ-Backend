from django.db import models

from trazeiq_backend.models import UUIDModel


class ErrorGroup(UUIDModel):
    """A deduplicated error signature — a fact about the codebase.

    One row per ``(project, fingerprint)``: repeated occurrences increment
    ``count`` instead of creating a new row (Agent.md constraint 3). The
    unique constraint doubles as the hot-path ``(project_id, fingerprint)``
    index the ingestion pipeline hits on every event.
    """

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="error_groups",
    )
    fingerprint = models.CharField(max_length=64)
    title = models.CharField(max_length=255)
    count = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()

    class Meta:
        ordering = ["-last_seen"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "fingerprint"],
                name="uniq_errorgroup_project_fingerprint",
            ),
        ]

    def __str__(self):
        return f"{self.title} x{self.count}"


class Event(UUIDModel):
    """One raw error occurrence, always persisted — even for the 500th repeat.

    ``message`` and ``stacktrace`` are stored post-redaction (the raw text is
    never persisted), per Agent.md constraint 5.
    """

    class Level(models.TextChoices):
        DEBUG = "debug", "Debug"
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
        FATAL = "fatal", "Fatal"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="events",
    )
    error_group = models.ForeignKey(
        ErrorGroup,
        on_delete=models.CASCADE,
        related_name="events",
    )
    message = models.TextField()
    stacktrace = models.TextField(blank=True, default="")
    level = models.CharField(
        max_length=16, choices=Level.choices, default=Level.ERROR
    )
    environment = models.CharField(max_length=32, blank=True, default="")
    service = models.CharField(max_length=64, blank=True, default="")
    endpoint = models.CharField(max_length=255, blank=True, default="")
    request_method = models.CharField(max_length=16, blank=True, default="")
    user_id = models.CharField(max_length=128, blank=True, default="")
    ip_address = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)
    fingerprint = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["project", "created_at"],
                name="idx_event_project_created",
            ),
        ]

    def __str__(self):
        return f"{self.level} {self.message[:60]}"