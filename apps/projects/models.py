import secrets

from django.conf import settings
from django.db import models

from trazeiq_backend.models import UUIDModel


class Project(UUIDModel):
    """An application watched by TrazeIQ.

    The raw API key is never stored — only its deterministic HMAC-SHA256
    digest, which the ingestion pipeline (Phase 1D) re-computes from the
    ``X-API-Key`` header to find the project.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(max_length=120)
    api_key_hash = models.CharField(max_length=64, db_index=True)
    api_key_prefix = models.CharField(max_length=16, editable=False)
    environment = models.CharField(max_length=32, default="production")
    events_per_minute = models.PositiveIntegerField(
        default=1000,
        help_text=(
            "Per-project ingestion cap (events/minute). Enforced by "
            "EventPerKeyRateThrottle; overrides the global EVENT_THROTTLE_KEY."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization"], name="idx_project_org"),
        ]

    def __str__(self):
        return f"{self.name} ({self.api_key_prefix}...)"
