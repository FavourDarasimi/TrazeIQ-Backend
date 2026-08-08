import datetime
from uuid import UUID

from .models import Event


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
    return qs.select_related("project", "error_group")


def get_event_for_user(event_id: UUID, user):
    """A single event the user can access, or ``None``.

    Scoped through membership like every tenant queryset — unknown ids and
    other orgs' events both resolve to ``None`` and surface as 404.
    """
    return _events_for_user(user).filter(id=event_id).first()


def parse_date_filter(value: str) -> datetime.date:
    """Parse ``?date=YYYY-MM-DD``, raising ValueError on malformed input."""
    return datetime.date.fromisoformat(value)