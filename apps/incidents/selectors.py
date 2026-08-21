"""Read-side queries for incidents — every queryset is membership-scoped
(Agent.md rule 2)."""

from uuid import UUID

from django.db.models import OuterRef, QuerySet, Subquery

from apps.events.models import Event

from .models import Incident, TimelineEntry


def _latest_event_id_subquery():
    """The most recent raw occurrence of an incident's error group.

    Computed as a scalar subquery so the list/detail views resolve it with
    one extra query (via :func:`latest_events_by_id`) instead of an N+1 —
    or a reverse-FK prefetch, which Django cannot slice.
    """
    return Event.objects.filter(
        error_group=OuterRef("error_group")
    ).order_by("-created_at").values("id")[:1]


def _incidents_for_user(user) -> QuerySet[Incident]:
    """Incidents in the caller's organizations — through membership, exactly
    like events/projects. Unknown and other-org ids resolve to empty."""
    return Incident.objects.filter(
        project__organization__memberships__user=user
    ).distinct()


def _with_latest_event(queryset: QuerySet[Incident]) -> QuerySet[Incident]:
    return queryset.annotate(
        latest_event_id=Subquery(_latest_event_id_subquery())
    ).select_related("project", "error_group")


def list_incidents_for_user(
    user,
    *,
    status=None,
    severity=None,
    project_id: UUID | None = None,
) -> QuerySet[Incident]:
    """Incidents the user can see, most recently-active first."""
    qs = _incidents_for_user(user)
    if status:
        qs = qs.filter(status=status)
    if severity:
        qs = qs.filter(severity=severity)
    if project_id:
        qs = qs.filter(project_id=project_id)
    return _with_latest_event(qs).order_by("-error_group__last_seen")


def get_incident_for_user(incident_id: UUID, user):
    """A single incident the user can access, or ``None``.

    Same 404-for-unknown-and-foreign contract as every tenant getter —
    cross-org access never surfaces as a permission error.
    """
    return (
        _with_latest_event(_incidents_for_user(user))
        .filter(id=incident_id)
        .first()
    )


def get_incidents_for_user(incident_ids: list[UUID], user) -> QuerySet[Incident]:
    """Multiple incidents the user can access, scoped by org membership.
    Returns only the subset of requested IDs that exist and are accessible."""
    return (
        _with_latest_event(_incidents_for_user(user))
        .filter(id__in=incident_ids)
    )


def get_updatable_incidents_for_user(incident_ids: list[UUID], user) -> QuerySet[Incident]:
    """Multiple incidents the user has developer+ role to modify."""
    from apps.organizations.models import MembershipRole

    allowed_roles = [
        MembershipRole.DEVELOPER,
        MembershipRole.ADMIN,
        MembershipRole.OWNER,
    ]
    return (
        Incident.objects.filter(
            id__in=incident_ids,
            project__organization__memberships__user=user,
            project__organization__memberships__role__in=allowed_roles,
        )
        .distinct()
        .select_related("project", "project__organization", "error_group")
    )


def latest_events_by_id(incidents) -> dict[UUID, Event]:
    """Resolve the annotated ``latest_event_id``s into Event rows, keyed by
    event id. Call after fetching incidents; ``incident.latest_event_id``
    stays ``None`` only when the group has no occurrences."""
    ids = {
        incident.latest_event_id
        for incident in incidents
        if incident.latest_event_id is not None
    }
    if not ids:
        return {}
    return {event.id: event for event in Event.objects.filter(id__in=ids)}


def list_incident_timeline(incident) -> list[dict]:
    """The incident's full history as uniform timeline rows, oldest first.

    Phase 1F served the raw occurrence feed only; Phase 4B merges the
    error group's ``Event`` rows (``kind="event"``) with the incident's
    ``TimelineEntry`` rows (``comment`` / ``status_change`` /
    ``ai_analysis``) into one chronological feed. Every row carries the
    full field set — event-only fields default to ``""`` and actor-less
    rows to ``None`` so the serializer shape never varies by kind.
    """
    events = (
        Event.objects.filter(error_group=incident.error_group)
        .order_by("created_at", "id")
        .only(
            "id",
            "level",
            "message",
            "environment",
            "service",
            "created_at",
        )
    )
    entries = (
        TimelineEntry.objects.filter(incident=incident)
        .select_related("actor")
        .order_by("created_at", "id")
    )
    rows = [
        {
            "id": event.id,
            "kind": TimelineEntry.Kind.EVENT,
            "level": event.level,
            "message": event.message,
            "environment": event.environment,
            "service": event.service,
            "content": "",
            "actor_email": None,
            "created_at": event.created_at,
        }
        for event in events
    ]
    rows += [
        {
            "id": entry.id,
            "kind": entry.kind,
            "level": "",
            "message": "",
            "environment": "",
            "service": "",
            "content": entry.content,
            "actor_email": entry.actor.email if entry.actor else None,
            "created_at": entry.created_at,
        }
        for entry in entries
    ]
    rows.sort(key=lambda row: row["created_at"])
    return rows
