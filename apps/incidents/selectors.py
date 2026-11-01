"""Read-side queries for incidents — every queryset is membership-scoped
(Agent.md rule 2)."""

from datetime import timedelta
from uuid import UUID

from django.db.models import OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from apps.events.models import Event
from apps.platform.search_parser import build_incident_search_q

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
    search: str | None = None,
) -> QuerySet[Incident]:
    """Incidents the user can see, most recently-active first.

    ``search`` is a portable substring match (``icontains`` — works on both
    SQLite dev and Postgres prod) across the error-group title and the
    group's event details (message, service, endpoint). Empty/blank search
    is a no-op so ``?search=`` never filters everything out.
    """
    qs = _incidents_for_user(user)
    if status:
        qs = qs.filter(status=status)
    if severity:
        qs = qs.filter(severity=severity)
    if project_id:
        qs = qs.filter(project_id=project_id)
    if search:
        search_q = build_incident_search_q(search[:300])
        if search_q:
            qs = qs.filter(search_q).distinct()
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


def get_incident_deploy_intelligence(incident: Incident) -> dict:
    """Analyze and correlate the incident with recent deployments to detect regressions.

    Returns suspect release/commits, first seen time, error rate before/after deploy,
    and affected endpoints with confidence scoring.
    """
    from apps.deployments.models import Deployment

    error_group = incident.error_group
    project = incident.project

    first_event = (
        Event.objects.filter(error_group=error_group)
        .order_by("created_at")
        .first()
    )

    correlated_deploy = None
    confidence = "low"

    # 1. Match deployment by first event's explicit release if present
    if first_event and first_event.release:
        correlated_deploy = (
            Deployment.objects.filter(
                project=project,
                release__version=first_event.release,
            )
            .select_related("release")
            .order_by("-deployed_at")
            .first()
        )
        if correlated_deploy:
            confidence = "high"

    # 2. Fall back to the latest completed deployment prior to or around group first_seen (within 48h)
    if correlated_deploy is None and error_group and error_group.first_seen:
        cutoff = error_group.first_seen - timedelta(hours=48)
        correlated_deploy = (
            Deployment.objects.filter(
                project=project,
                deployed_at__lte=error_group.first_seen,
                deployed_at__gte=cutoff,
                status__in=[Deployment.Status.COMPLETED, Deployment.Status.HEALTHY],
            )
            .select_related("release")
            .order_by("-deployed_at")
            .first()
        )
        if correlated_deploy:
            delta_seconds = (error_group.first_seen - correlated_deploy.deployed_at).total_seconds()
            if delta_seconds <= 7200:  # within 2 hours
                confidence = "high"
            elif delta_seconds <= 86400:  # within 24 hours
                confidence = "medium"
            else:
                confidence = "low"

    if correlated_deploy is None:
        return {
            "has_regression": False,
            "likely_release": None,
            "first_seen": error_group.first_seen.isoformat() if error_group and error_group.first_seen else None,
            "affected_endpoints": [],
            "error_rate_before": 0.0,
            "error_rate_after": 0.0,
            "events_count_before": 0,
            "events_count_after": 0,
            "rate_change_percent": 0.0,
            "confidence": "low",
        }

    rel = correlated_deploy.release
    t_deploy = correlated_deploy.deployed_at
    now = timezone.now()

    # Calculate error rate in 1-hour window before vs 1-hour window after
    before_start = t_deploy - timedelta(hours=1)
    before_end = t_deploy
    after_start = t_deploy
    after_end = min(t_deploy + timedelta(hours=1), now)

    events_before = Event.objects.filter(
        error_group=error_group,
        created_at__gte=before_start,
        created_at__lt=before_end,
    ).count()

    events_after = Event.objects.filter(
        error_group=error_group,
        created_at__gte=after_start,
        created_at__lte=after_end,
    ).count()

    hours_before = 1.0
    hours_after = max(0.01, (after_end - after_start).total_seconds() / 3600.0)

    rate_before = round(events_before / hours_before, 2)
    rate_after = round(events_after / hours_after, 2)

    if events_before == 0:
        rate_change = 100.0 if events_after > 0 else 0.0
    else:
        rate_change = round(((rate_after - rate_before) / rate_before) * 100, 1)

    # Distinct affected endpoints
    endpoints = list(
        Event.objects.filter(error_group=error_group)
        .exclude(endpoint="")
        .values_list("endpoint", flat=True)
        .distinct()[:10]
    )

    return {
        "has_regression": True,
        "likely_release": {
            "id": str(rel.pk),
            "version": rel.version,
            "commit_sha": rel.commit_sha,
            "commit_message": rel.commit_message,
            "commit_author": rel.commit_author,
            "commit_url": rel.commit_url,
            "pull_request_url": rel.pull_request_url,
            "pull_request_title": rel.pull_request_title,
            "deployed_at": t_deploy.isoformat(),
            "environment": correlated_deploy.environment,
        },
        "first_seen": error_group.first_seen.isoformat() if error_group and error_group.first_seen else None,
        "affected_endpoints": endpoints,
        "error_rate_before": rate_before,
        "error_rate_after": rate_after,
        "events_count_before": events_before,
        "events_count_after": events_after,
        "rate_change_percent": rate_change,
        "confidence": confidence,
    }

