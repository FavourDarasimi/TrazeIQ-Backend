"""Read-only platform aggregates: totals, per-tenant lists, pipeline health.

Unlike the tenant dashboard selectors (which scope every queryset to the
caller's organizations per Agent.md rule 2), these queries intentionally span
all tenants — that is the point of platform monitoring. They stay behind
``IsStaff`` and stay cheap:

- totals are ``.count()`` calls on indexed columns,
- per-row numbers (member/project/event counts) are SQL annotations, not
  Python loops — one query per page, no N+1,
- the overview aggregate is TTL-cached (see ``apps/platform/cache.py``).
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.models import User
from apps.ai.models import AIAnalysis
from apps.alerts.models import AlertLog
from apps.auditlog.models import AuditLog
from apps.events.models import Event
from apps.incidents.models import Incident
from apps.organizations.models import Organization
from apps.projects.models import Project

OPEN_STATUSES = (Incident.Status.OPEN, Incident.Status.INVESTIGATING)
SEVERITIES = [choice[0] for choice in Incident.Severity.choices]


def platform_totals() -> dict:
    """High-signal platform counts for the admin overview cards."""
    now = timezone.now()
    day_ago = now - timedelta(hours=24)
    open_by_severity = dict(
        Incident.objects.filter(status__in=OPEN_STATUSES)
        .values("severity")
        .annotate(n=Count("id"))
        .values_list("severity", "n")
    )
    ai_rows = (
        AIAnalysis.objects.filter(created_at__gte=day_ago)
        .values("status")
        .annotate(n=Count("id"))
    )
    return {
        "users": User.objects.count(),
        "organizations": Organization.objects.count(),
        "projects": Project.objects.count(),
        "events_24h": Event.objects.filter(created_at__gte=day_ago).count(),
        "open_incidents": sum(open_by_severity.get(s, 0) for s in SEVERITIES),
        "open_incidents_by_severity": {
            severity: open_by_severity.get(severity, 0)
            for severity in SEVERITIES
        },
        "ai_24h": {
            row["status"]: row["n"] for row in ai_rows
        },
        "alerts_24h": AlertLog.objects.filter(
            dispatched_at__gte=day_ago
        ).count(),
    }


def top_projects_by_volume(limit: int = 5) -> list[dict]:
    """Projects with the most events in the last 24h (one aggregate query)."""
    day_ago = timezone.now() - timedelta(hours=24)
    rows = (
        Project.objects.select_related("organization")
        .annotate(
            events_24h=Count(
                "events", filter=Q(events__created_at__gte=day_ago)
            ),
            open_incidents=Count(
                "incidents", filter=Q(incidents__status__in=OPEN_STATUSES)
            ),
        )
        .order_by("-events_24h")[:limit]
    )
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "organization": row.organization.name,
            "events_24h": row.events_24h,
            "open_incidents": row.open_incidents,
        }
        for row in rows
    ]


def recent_audit_entries(limit: int = 10) -> list[dict]:
    """Latest privileged actions platform-wide (who did what, where)."""
    rows = (
        AuditLog.objects.select_related("actor", "organization")
        .order_by("-created_at")[:limit]
    )
    return [
        {
            "id": str(row.id),
            "action": row.action,
            "actor_email": row.actor.email,
            "organization": row.organization.name,
            "target": row.target,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def platform_overview() -> dict:
    """The full overview payload: totals + top projects + recent audit."""
    return {
        **platform_totals(),
        "top_projects": top_projects_by_volume(),
        "recent_audit": recent_audit_entries(),
    }


def list_users(search: str = ""):
    """All accounts, newest first — annotated with their org count."""
    qs = User.objects.annotate(
        org_count=Count("memberships", distinct=True)
    ).order_by("-date_joined")
    if search:
        qs = qs.filter(email__icontains=search)
    return qs


def list_organizations(search: str = ""):
    """All tenants with member/project counts (single annotated query)."""
    qs = (
        Organization.objects.select_related("owner")
        .annotate(
            member_count=Count("memberships", distinct=True),
            project_count=Count("projects", distinct=True),
        )
        .order_by("-created_at")
    )
    if search:
        qs = qs.filter(name__icontains=search)
    return qs


def list_projects(search: str = ""):
    """All projects with 24h volume + open incidents (single query)."""
    day_ago = timezone.now() - timedelta(hours=24)
    qs = (
        Project.objects.select_related("organization")
        .annotate(
            events_24h=Count(
                "events", filter=Q(events__created_at__gte=day_ago)
            ),
            open_incidents=Count(
                "incidents", filter=Q(incidents__status__in=OPEN_STATUSES)
            ),
        )
        .order_by("-created_at")
    )
    if search:
        qs = qs.filter(name__icontains=search)
    return qs
