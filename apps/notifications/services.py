"""Notification delivery: turn incident lifecycle events into inbox rows.

Every ``notify_*`` entry point is best-effort — it never raises — so the
ingestion path and incident mutations can call them without a try/except
(they must keep responding in milliseconds regardless of anything here).

Audience rules (per recipient's ``AlertPreference``):

- The acting user never notifies themselves.
- Assignments always notify the assignee — that is the one event that is
  unambiguously "to me", even under ``only_assigned_to_me``.
- Under ``only_assigned_to_me`` every other kind only reaches the incident's
  assignee; otherwise the per-kind toggles decide (defaults: all on).
"""

import logging

from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from apps.incidents.models import Incident
from apps.organizations.models import Membership

from .models import AlertPreference, Notification

logger = logging.getLogger(__name__)


def _org_member_ids(incident: Incident) -> list[UUID]:
    return list(
        Membership.objects.filter(
            organization_id=incident.project.organization_id
        ).values_list("user_id", flat=True)
    )


def _defaults(incident: Incident, recipient_id: UUID, kind: str) -> bool:
    """The audience filter for recipients without a preference row — the
    same defaults as ``AlertPreference``: everything on, assigned-only off."""
    if kind == Notification.Kind.INCIDENT_ASSIGNED:
        return True
    if incident.assigned_to_id is not None and incident.assigned_to_id != recipient_id:
        return False
    return True


def _should_notify(
    preference: AlertPreference | None,
    incident: Incident,
    kind: str,
    recipient_id: UUID,
) -> bool:
    """Apply the recipient's preference row (or defaults) to one kind."""
    if kind == Notification.Kind.INCIDENT_ASSIGNED:
        return True
    if preference is None:
        return _defaults(incident, recipient_id, kind)
    if preference.only_assigned_to_me:
        if incident.assigned_to_id != recipient_id:
            return False
    if kind == Notification.Kind.INCIDENT_CREATED:
        return preference.notify_on_new_incidents
    if kind == Notification.Kind.INCIDENT_COMMENTED:
        return preference.notify_on_comments
    return preference.notify_on_status_changes


def _deliver(
    incident: Incident,
    *,
    kind: str,
    title: str,
    body: str = "",
    actor_id: UUID | None = None,
    recipients: list[UUID] | None = None,
) -> int:
    """Insert one inbox row per eligible recipient; returns the row count.

    The preference map is fetched in one query; rows are bulk-created. All
    failures are logged and swallowed — notification delivery must never
    fail the caller.
    """
    try:
        recipient_ids = recipients or _org_member_ids(incident)
        preferences = {
            pref.user_id: pref
            for pref in AlertPreference.objects.filter(user_id__in=recipient_ids)
        }
        rows = []
        for recipient_id in recipient_ids:
            if actor_id is not None and recipient_id == actor_id:
                continue
            if not _should_notify(
                preferences.get(recipient_id), incident, kind, recipient_id
            ):
                continue
            rows.append(
                Notification(
                    recipient_id=recipient_id,
                    incident=incident,
                    kind=kind,
                    title=title,
                    body=body,
                )
            )
        if rows:
            Notification.objects.bulk_create(rows)
        return len(rows)
    except Exception:  # noqa: BLE001 — best-effort delivery contract.
        logger.exception(
            "notification delivery failed for %s incident %s",
            kind,
            incident.pk,
        )
        return 0


def notify_incident_created(incident: Incident) -> int:
    """New incident opened in the project's organization."""
    return _deliver(
        incident,
        kind=Notification.Kind.INCIDENT_CREATED,
        title=f"New {incident.get_severity_display().lower()} incident",
        body=incident.error_group.title,
    )


def notify_incident_assigned(incident: Incident) -> int:
    """An incident was assigned — always reaches the assignee."""
    assignee_id = incident.assigned_to_id
    if assignee_id is None:
        return 0
    return _deliver(
        incident,
        kind=Notification.Kind.INCIDENT_ASSIGNED,
        title="Incident assigned to you",
        body=incident.error_group.title,
        recipients=[assignee_id],
    )


def notify_incident_updated(
    incident: Incident, *, actor_id: UUID | None = None, changes: list[str]
) -> int:
    """Status/severity changed on an incident."""
    return _deliver(
        incident,
        kind=Notification.Kind.INCIDENT_UPDATED,
        title=f"Incident {incident.status}",
        body=", ".join(changes)[:500],
        actor_id=actor_id,
    )


def notify_incident_commented(
    incident: Incident,
    *,
    actor_id: UUID | None = None,
    content: str = "",
) -> int:
    """A comment landed on an incident's timeline."""
    return _deliver(
        incident,
        kind=Notification.Kind.INCIDENT_COMMENTED,
        title="New comment on incident",
        body=content[:500],
        actor_id=actor_id,
    )


def notify_incident_resolved(
    incident: Incident, *, actor_id: UUID | None = None
) -> int:
    """An incident was marked resolved."""
    return _deliver(
        incident,
        kind=Notification.Kind.INCIDENT_RESOLVED,
        title="Incident resolved",
        body=incident.error_group.title,
        actor_id=actor_id,
    )


def mark_notifications_read(
    user, *, notification_ids: list[UUID] | None = None
) -> int:
    """Mark the caller's notifications read — the given ids, or every row
    when no ids are provided. Returns how many rows were updated."""
    queryset: QuerySet[Notification] = Notification.objects.filter(
        recipient=user, is_read=False
    )
    if notification_ids:
        queryset = queryset.filter(id__in=notification_ids)
    return queryset.update(is_read=True, read_at=timezone.now())


__all__ = [
    "mark_notifications_read",
    "notify_incident_assigned",
    "notify_incident_commented",
    "notify_incident_created",
    "notify_incident_resolved",
    "notify_incident_updated",
]