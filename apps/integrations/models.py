"""Phase 4D: third-party integrations, owned per organization.

The Slack access token is the sensitive piece: stored via django-
cryptography's ``EncryptedCharField`` so a leaked database dump yields
ciphertext, never a usable token.
"""

from django.db import models

from trazeiq_backend.models import UUIDModel

from .fields import EncryptedCharField


class SlackIntegration(UUIDModel):
    """An organization's Slack workspace connection (OAuth, Phase 4D)."""

    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="slack_integration",
    )
    access_token = EncryptedCharField(
        max_length=512,
        help_text="Encrypted at rest (Fernet, key derived from SECRET_KEY).",
    )
    team_name = models.CharField(max_length=120, blank=True, default="")
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-connected_at"]

    def __str__(self):
        return f"Slack @ {self.organization_id} ({self.team_name})"