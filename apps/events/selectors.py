import datetime
from uuid import UUID

from django.db.models import Q

from .models import ErrorGroup, Event


def _events_for_user(user):
    """Every queryset touching Event is scoped to the caller's organization
    (Agent.md rule 2) — through membership, exactly like projects."""
    return Event.objects.filter(
        project__organization__memberships__user=user
    ).distinct()


def list_events_for_user(
    user,
    *,
    level=None,
    environment=None,
    service=None,
    date=None,
    search=None,
):
    qs = _events_for_user(user)
    if level:
        qs = qs.filter(level=level)
    if environment:
        qs = qs.filter(environment=environment)
    if service:
        qs = qs.filter(service__icontains=service)
    if date:
        qs = qs.filter(created_at__date=date)
    if search:
        qs = qs.filter(
            Q(message__icontains=search)
            | Q(fingerprint__icontains=search)
            | Q(service__icontains=search)
            | Q(environment__icontains=search)
            | Q(endpoint__icontains=search)
        )
    # Newest first — the log-stream convention; the tiebreaker keeps the
    # order deterministic for pagination.
    return qs.select_related("project", "error_group").order_by(
        "-created_at", "-id"
    )


def get_event_for_user(event_id: UUID, user):
    """A single event the user can access, or ``None``.

    Scoped through membership like every tenant queryset — unknown ids and
    other orgs' events both resolve to ``None`` and surface as 404.
    """
    return _events_for_user(user).filter(id=event_id).first()


def parse_date_filter(value: str) -> datetime.date:
    """Parse ``?date=YYYY-MM-DD``, raising ValueError on malformed input."""
    return datetime.date.fromisoformat(value)


def _error_groups_for_user(user):
    """ErrorGroups in the caller's organizations (Agent.md rule 2)."""
    return ErrorGroup.objects.filter(
        project__organization__memberships__user=user
    ).distinct()


def list_error_groups_for_user(user, *, project_id: UUID | None = None):
    """Deduplicated error signatures, most recently-seen first.

    Optional ``project_id`` narrows to one project; unknown/foreign ids
    resolve to empty (never a leak).
    """
    qs = _error_groups_for_user(user)
    if project_id is not None:
        qs = qs.filter(project_id=project_id)
    return qs.select_related("project").order_by("-last_seen", "-id")