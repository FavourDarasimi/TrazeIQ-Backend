"""Read-side aggregate queries for the dashboard overview and stats pages.

Every queryset is membership-scoped (Agent.md rule 2): a caller only ever
sees aggregates for their own organizations, and an unknown/foreign
``project_id`` simply narrows to an empty intersection instead of leaking
existence.
"""

from uuid import UUID

from django.db.models import Count, QuerySet
from django.db.models.functions import TruncDay, TruncHour
from django.utils import timezone
from datetime import timedelta

from apps.events.models import ErrorGroup, Event
from apps.incidents.models import Incident
from apps.projects.models import Project

OPEN_STATUSES = (Incident.Status.OPEN, Incident.Status.INVESTIGATING)
SEVERITIES = [choice[0] for choice in Incident.Severity.choices]

RANGE_BUCKETS = {
    "24h": ("hours", 24),
    "7d": ("days", 7),
    "30d": ("days", 30),
}


def _visible_projects(user) -> QuerySet[Project]:
    """Projects in the caller's organizations, as a plain subquery.

    Aggregates over a ``project__in=<subquery>`` filter avoid the join —
    a multi-valued membership join would force DISTINCT, which corrupts
    ``GROUP BY`` bucket counts.
    """
    return Project.objects.filter(organization__memberships__user=user)


def _membership_incidents(user) -> QuerySet[Incident]:
    return Incident.objects.filter(project__in=_visible_projects(user))


def _membership_events(user) -> QuerySet[Event]:
    return Event.objects.filter(project__in=_visible_projects(user))


def _membership_error_groups(user) -> QuerySet[ErrorGroup]:
    return ErrorGroup.objects.filter(project__in=_visible_projects(user))


def overview_for_user(user, project_id: UUID | None = None) -> dict:
    """The overview aggregate: open incidents by severity, recent event
    volume and trend, resolved count, top recurring errors, health."""

    now = timezone.now()
    window_start = now - timedelta(hours=24)
    prev_start = now - timedelta(hours=48)
    trend_start = now - timedelta(days=7)

    incidents = _membership_incidents(user)
    events = _membership_events(user)
    if project_id:
        incidents = incidents.filter(project_id=project_id)
        events = events.filter(project_id=project_id)

    by_severity = dict(
        incidents.filter(status__in=OPEN_STATUSES)
        .values("severity")
        .annotate(count=Count("id"))
        .values_list("severity", "count")
    )
    open_by_severity = {severity: by_severity.get(severity, 0) for severity in SEVERITIES}

    events_24h = events.filter(created_at__gte=window_start).count()
    events_prev_24h = events.filter(
        created_at__gte=prev_start, created_at__lt=window_start
    ).count()
    if events_prev_24h:
        change = round((events_24h - events_prev_24h) / events_prev_24h * 100)
    else:
        change = 100 if events_24h else 0
    trend = "up" if change > 0 else "down" if change < 0 else "flat"

    resolved_24h = incidents.filter(
        status=Incident.Status.RESOLVED, resolved_at__gte=window_start
    ).count()

    groups_qs = _membership_error_groups(user).filter(last_seen__gte=trend_start)
    if project_id:
        groups_qs = groups_qs.filter(project_id=project_id)
    groups = list(groups_qs.order_by("-count")[:5])

    top_errors = []
    if groups:
        open_incidents = {
            incident.error_group_id: incident
            for incident in incidents.filter(
                error_group__in=[g.id for g in groups],
                status__in=OPEN_STATUSES,
            )
        }
        for group in groups:
            incident = open_incidents.get(group.id)
            top_errors.append(
                {
                    "fingerprint": group.fingerprint,
                    "title": group.title,
                    "count": group.count,
                    "last_seen": group.last_seen,
                    "incident_id": str(incident.id) if incident else None,
                    "severity": incident.severity if incident else None,
                }
            )

    critical = open_by_severity["critical"]
    high = open_by_severity["high"]
    if critical:
        health = "critical"
    elif high:
        health = "degraded"
    else:
        health = "healthy"

    return {
        "open_incidents": {
            "total": sum(open_by_severity.values()),
            "by_severity": open_by_severity,
        },
        "events_24h": events_24h,
        "event_trend": {"percent_change": change, "trend": trend},
        "resolved_24h": resolved_24h,
        "top_errors": top_errors,
        "health": health,
    }


def stats_for_user(user, project_id: UUID | None, range_: str) -> list[dict]:
    """Bucketed time-series: event and incident-creation counts per bucket.

    Missing buckets are zero-filled so the chart never has gaps.
    """
    granularity, span = RANGE_BUCKETS[range_]
    if granularity == "hours":
        truncate, step, unit = TruncHour, timedelta(hours=1), "hours"
    else:
        truncate, step = TruncDay, timedelta(days=1)
        unit = None

    now = timezone.now()
    if range_ == "24h":
        start = (now - timedelta(hours=24)).replace(
            minute=0, second=0, microsecond=0
        )
    else:
        days = 7 if range_ == "7d" else 30
        start = (now - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    events = _membership_events(user)
    incidents = _membership_incidents(user)
    if project_id:
        events = events.filter(project_id=project_id)
        incidents = incidents.filter(project_id=project_id)

    event_counts = {
        row["bucket"]: row["count"]
        for row in events.filter(created_at__gte=start)
        .annotate(bucket=truncate("created_at"))
        .values("bucket")
        .annotate(count=Count("id"))
    }
    incident_counts = {
        row["bucket"]: row["count"]
        for row in incidents.filter(created_at__gte=start)
        .annotate(bucket=truncate("created_at"))
        .values("bucket")
        .annotate(count=Count("id"))
    }

    points = []
    bucket = start
    while bucket < now:
        points.append(
            {
                "ts": bucket.isoformat(),
                "events": event_counts.get(bucket, 0),
                "incidents": incident_counts.get(bucket, 0),
            }
        )
        bucket += step
    return points