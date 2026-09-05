"""Read-side queries for SLOs — every queryset is membership-scoped
(Agent.md rule 2)."""

from uuid import UUID

from django.db.models import QuerySet

from apps.projects.models import Project

from .models import SLO, SLOBreachAlert, SLOBudgetSnapshot


def _slos_for_user(user) -> QuerySet[SLO]:
    return SLO.objects.filter(
        project__organization__memberships__user=user
    ).distinct()


def list_slos_for_user(user, *, project_id: UUID | None = None) -> QuerySet[SLO]:
    """SLOs in the caller's organizations, newest first. Optional
    ``project_id`` filter narrows to one project."""
    qs = _slos_for_user(user).select_related("project")
    if project_id:
        qs = qs.filter(project_id=project_id)
    return qs


def get_slo_for_user(slo_id: UUID, user):
    """A single SLO the user can access, or ``None``. Unknown and foreign
    ids resolve to ``None`` and surface as 404 — never a leak."""
    return (
        _slos_for_user(user).select_related("project").filter(id=slo_id).first()
    )


def list_snapshots_for_slo(slo: SLO, *, limit: int = 200) -> QuerySet[SLOBudgetSnapshot]:
    """Latest snapshots for the SLO, newest first."""
    return SLOBudgetSnapshot.objects.filter(slo=slo).order_by(
        "-evaluated_at"
    )[:limit]


def latest_snapshot_for_slo(slo: SLO):
    """The most recent snapshot, or ``None`` when the SLO has never been
    evaluated yet. ``first()`` on an already-limited queryset keeps this
    a single round-trip."""
    return SLOBudgetSnapshot.objects.filter(slo=slo).order_by(
        "-evaluated_at"
    ).first()


def list_alerts_for_slo(slo: SLO, *, limit: int = 100) -> QuerySet[SLOBreachAlert]:
    """Recent breach alerts for one SLO, newest first."""
    return SLOBreachAlert.objects.filter(slo=slo).select_related(
        "acknowledged_by"
    ).order_by("-fired_at")[:limit]


def list_recent_alerts_for_org(user, *, limit: int = 50):
    """Recent breach alerts across every SLO the user can access — drives
    the cross-project service-dependency view."""
    return (
        SLOBreachAlert.objects.filter(
            slo__project__organization__memberships__user=user,
        )
        .select_related("slo", "slo__project")
        .order_by("-fired_at")[:limit]
    )


def services_with_slos(user) -> dict[str, list[SLO]]:
    """The SLOs the user can see, grouped by ``error_query.service``.

    SLOs without a service filter (watching the whole project) are returned
    under the empty-string key ``""`` so the dependency-impact view can
    treat them as "affects everything" downstream.
    """
    grouped: dict[str, list[SLO]] = {}
    for slo in _slos_for_user(user).filter(enabled=True).order_by("name"):
        service = (slo.error_query or {}).get("service", "") or ""
        grouped.setdefault(service, []).append(slo)
    return grouped


def projects_visible_to_user(user) -> QuerySet[Project]:
    """Projects the user can attach an SLO to. Used by the create form's
    project picker."""
    return Project.objects.filter(
        organization__memberships__user=user
    ).select_related("organization")