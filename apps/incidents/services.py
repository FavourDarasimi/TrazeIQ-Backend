"""Incident workflow mutations (Phase 4A): status/severity/assignment updates.

Every status mutation appends a ``status_change`` TimelineEntry (the same
contract the resolve action follows) so the incident's history stays complete.
"""

from django.utils import timezone

from .models import Incident, TimelineEntry

_UNSET = object()


def update_incident(
    incident: Incident,
    *,
    actor=None,
    status=_UNSET,
    severity=_UNSET,
    assigned_to=_UNSET,
) -> Incident:
    """Apply partial workflow changes to an incident.

    Only provided fields change; ``_UNSET`` distinguishes "not provided" from
    an explicit ``None`` (which clears the assignment). Any effective change
    is recorded as a ``status_change`` timeline entry, and ``resolved_at`` is
    kept consistent with the status.
    """
    changes = []
    assignment_changed = False
    state_changed = False

    if status is not _UNSET and status != incident.status:
        incident.status = status
        if status == Incident.Status.RESOLVED and incident.resolved_at is None:
            incident.resolved_at = timezone.now()
        elif status != Incident.Status.RESOLVED:
            incident.resolved_at = None
        changes.append(f"status → {status}")
        state_changed = True

    if severity is not _UNSET and severity != incident.severity:
        incident.severity = severity
        changes.append(f"severity → {severity}")
        state_changed = True

    if assigned_to is not _UNSET and assigned_to != incident.assigned_to:
        incident.assigned_to = assigned_to
        changes.append(f"assigned_to → {assigned_to.email if assigned_to else 'unassigned'}")
        assignment_changed = True

    if not changes:
        return incident

    incident.save(update_fields=["status", "severity", "assigned_to", "resolved_at"])
    TimelineEntry.objects.create(
        incident=incident,
        kind=TimelineEntry.Kind.STATUS_CHANGE,
        content=", ".join(changes)[:1000],
        actor=actor,
    )

    # Best-effort inbox fan-out — never fails the update request.
    from apps.notifications.services import (
        notify_incident_assigned,
        notify_incident_updated,
    )

    if assignment_changed and incident.assigned_to is not None:
        notify_incident_assigned(incident)
    if state_changed:
        notify_incident_updated(
            incident, actor_id=actor.pk if actor else None, changes=changes
        )
    return incident


def add_comment(incident: Incident, *, content: str, actor) -> TimelineEntry:
    """Append a ``comment`` TimelineEntry to the incident's history.

    The entry appears in the timeline feed immediately; the caller must
    already have verified the actor is an organization member (developer+)
    and is expected to push the ``incident.updated`` realtime event.
    """
    entry = TimelineEntry.objects.create(
        incident=incident,
        kind=TimelineEntry.Kind.COMMENT,
        content=content.strip(),
        actor=actor,
    )

    # Best-effort inbox fan-out — never fails the comment request.
    from apps.notifications.services import notify_incident_commented

    notify_incident_commented(
        incident, actor_id=actor.pk if actor else None, content=content.strip()
    )
    return entry


def bulk_update_incidents(
    incidents: list[Incident],
    *,
    actor,
    status=_UNSET,
    severity=_UNSET,
    assigned_to=_UNSET,
) -> list[Incident]:
    """Apply the same workflow changes to multiple incidents in one pass.

    Delegates per-incident logic to ``update_incident`` so timeline entries,
    notifications, and resolved_at bookkeeping stay consistent.
    """
    updated = []
    for incident in incidents:
        res = update_incident(
            incident,
            actor=actor,
            status=status,
            severity=severity,
            assigned_to=assigned_to,
        )
        updated.append(res)
    return updated
