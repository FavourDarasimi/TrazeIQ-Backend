"""In-app notification inbox and per-user alert preferences.

A ``Notification`` is one unread/read row in a user's inbox, always tied to
the incident (or system event) that produced it. ``AlertPreference`` is the
per-user noise filter: the headline knob is ``only_assigned_to_me`` ("Only
notify me on incidents assigned to me"), with per-kind toggles for new
incidents, status changes and comments.

Emission is best-effort by contract: ``notify_*`` services never raise, so a
notification write can never fail the ingestion or incident-update request
(mirroring the alert-evaluation and Pusher hooks).
"""

from django.conf import settings
from django.db import models

from trazeiq_backend.models import UUIDModel

from apps.incidents.models import Incident


class Notification(UUIDModel):
    """One inbox row for one recipient."""

    class Kind(models.TextChoices):
        INCIDENT_CREATED = "incident_created", "Incident created"
        INCIDENT_ASSIGNED = "incident_assigned", "Incident assigned"
        INCIDENT_UPDATED = "incident_updated", "Incident updated"
        INCIDENT_COMMENTED = "incident_commented", "Incident commented"
        INCIDENT_RESOLVED = "incident_resolved", "Incident resolved"
        SYSTEM = "system", "System"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    incident = models.ForeignKey(
        Incident,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["recipient", "is_read"],
                name="idx_notif_recipient_read",
            ),
            models.Index(
                fields=["recipient", "created_at"],
                name="idx_notif_recipient_created",
            ),
        ]

    def __str__(self):
        return f"{self.kind} -> {self.recipient_id}"


class AlertPreference(UUIDModel):
    """One row per user — the inbox noise filter (defaults = notify on all
    in-org incident activity)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="alert_preferences",
    )
    only_assigned_to_me = models.BooleanField(
        default=False,
        help_text=(
            "Only notify me about incidents assigned to me; in-org activity "
            "on other incidents is silenced."
        ),
    )
    notify_on_new_incidents = models.BooleanField(default=True)
    notify_on_status_changes = models.BooleanField(default=True)
    notify_on_comments = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"prefs for {self.user_id}"