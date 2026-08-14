"""Phase 3C: dashboard aggregate endpoints.

Read-only, JWT auth, membership-scoped through ``apps.analytics.selectors``.
"""

from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from apps.projects.models import Project
from trazeiq_backend.responses import api_success, envelope_schema

from .cache import cached_dashboard
from .selectors import RANGE_BUCKETS, overview_for_user, stats_for_user

VALID_RANGES = set(RANGE_BUCKETS)


def _project_id(request) -> UUID | None:
    raw = request.query_params.get("project_id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        raise ValidationError(
            {"project_id": ["A valid UUID is required."]}
        )


def _request_project_scope(request, project_id: UUID | None) -> list[UUID]:
    """The project ids whose versions belong in the cache key.

    Project-scoped requests cache under that one project; unscoped requests
    key on every project the caller can see. The selector still enforces
    membership, so a foreign ``project_id`` resolves to an empty (correct)
    aggregate and never leaks another tenant's data.
    """
    if project_id is not None:
        return [project_id]
    return list(
        Project.objects.filter(
            organization__memberships__user=request.user
        ).values_list("id", flat=True)
    )


class DashboardOverviewView(APIView):
    """GET /api/dashboard/overview/ — current health snapshot for the user's
    projects (optionally narrowed to one project)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["dashboard"],
        operation_id="dashboard_overview",
        summary="Dashboard overview",
        description=(
            "Open incident counts by severity, 24h event volume with trend, "
            "resolved-in-24h count, top recurring errors (last 7 days of "
            "activity) and a derived system-health level. Optional "
            "``project_id`` narrows every stat to one project; without it, "
            "everything aggregates across the caller's organizations."
        ),
        responses={
            200: envelope_schema(
                "DashboardOverviewOk",
                payload={
                    "overview": {
                        "open_incidents": {
                            "total": 0,
                            "by_severity": {
                                "critical": 0,
                                "high": 0,
                                "medium": 0,
                                "low": 0,
                            },
                        },
                        "events_24h": 0,
                        "event_trend": {"percent_change": 0, "trend": "flat"},
                        "resolved_24h": 0,
                        "top_errors": [
                            {
                                "fingerprint": "",
                                "title": "",
                                "count": 0,
                                "last_seen": "",
                                "incident_id": "",
                                "severity": "",
                            }
                        ],
                        "health": "healthy",
                    }
                },
            )
        },
    )
    def get(self, request):
        project_id = _project_id(request)
        project_ids = _request_project_scope(request, project_id)
        overview = cached_dashboard(
            "overview",
            project_ids,
            None,
            lambda: overview_for_user(request.user, project_id=project_id),
        )
        return api_success({"overview": overview})


class DashboardStatsView(APIView):
    """GET /api/dashboard/stats/?range=24h|7d|30d — bucketed time-series."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["dashboard"],
        operation_id="dashboard_stats",
        summary="Dashboard time-series",
        description=(
            "Event and incident counts bucketed per hour (24h) or per day "
            "(7d/30d), zero-filled so the chart has no gaps."
        ),
        responses={
            200: envelope_schema(
                "DashboardStatsOk",
                payload={
                    "stats": {
                        "range": "7d",
                        "points": [
                            {"ts": "2026-01-01T00:00:00+00:00", "events": 0, "incidents": 0}
                        ],
                    }
                },
            )
        },
    )
    def get(self, request):
        range_ = request.query_params.get("range") or "24h"
        if range_ not in VALID_RANGES:
            raise ValidationError(
                {"range": [f"Must be one of: {', '.join(sorted(VALID_RANGES))}."]}
            )
        project_id = _project_id(request)
        project_ids = _request_project_scope(request, project_id)
        points = cached_dashboard(
            "stats",
            project_ids,
            range_,
            lambda: stats_for_user(
                request.user, project_id=project_id, range_=range_
            ),
        )
        return api_success({"stats": {"range": range_, "points": points}})