from django.conf import settings
from django.db import models

from apps.organizations.models import Organization
from trazeiq_backend.models import UUIDModel


class AuditAction(models.TextChoices):
    KEY_ROTATED = "key_rotated", "API key rotated"
    MEMBER_REMOVED = "member_removed", "Member removed"
    INCIDENT_RESOLVED = "incident_resolved", "Incident resolved"


class AuditLog(UUIDModel):
    """An immutable record of a privileged, security-relevant action.

    Captures who did it (``actor``), in which tenant (``organization``), what
    happened (``action``) and the human-readable subject (``target``).
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="audit_log_entries",
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    target = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} by {self.actor_id}: {self.target}"
