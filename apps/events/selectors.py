import datetime
import re
from uuid import UUID

from django.db.models import Q

from apps.platform.search_parser import build_event_search_q

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
    user_id=None,
    release=None,
    tag=None,
    project_id: UUID | None = None,
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
    if user_id:
        qs = qs.filter(user_id__icontains=user_id)
    if release:
        qs = qs.filter(
            Q(metadata__release__icontains=release)
            | Q(metadata__version__icontains=release)
            | Q(metadata__app_version__icontains=release)
        )
    if tag:
        if ":" in tag:
            raw_key, _, raw_val = tag.partition(":")
            clean_key = re.sub(r"[^a-zA-Z0-9_\-]", "", raw_key.strip())
            val = raw_val.strip()
            if clean_key and val:
                qs = qs.filter(
                    Q(**{f"metadata__{clean_key}__icontains": val})
                    | Q(**{f"metadata__tags__{clean_key}__icontains": val})
                )
            elif clean_key:
                qs = qs.filter(
                    Q(**{f"metadata__{clean_key}__isnull": False})
                    | Q(**{f"metadata__tags__{clean_key}__isnull": False})
                )
            else:
                qs = qs.filter(metadata__icontains=tag)
        else:
            qs = qs.filter(metadata__icontains=tag)
    if project_id is not None:
        qs = qs.filter(project_id=project_id)
    if search:
        search_q = build_event_search_q(search[:300])
        if search_q:
            qs = qs.filter(search_q)
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