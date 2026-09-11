"""Platform admin endpoints — site-wide monitoring for TrazeIQ operators.

Read-only by design (v1 monitors; it mutates nothing — the smallest possible
attack surface for a cross-tenant surface). Every view requires ``IsStaff``,
is throttled per user (``ADMIN_THROTTLE``), and every list is paginated with
search — never an unbounded queryset.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from trazeiq_backend.responses import api_success, envelope_schema
from trazeiq_backend.views import (
    _check_cache,
    _check_database,
    _check_worker,
    _pipeline_metrics,
)

from .cache import cached_overview
from .permissions import IsStaff
from .selectors import (
    list_organizations,
    list_projects,
    list_users,
    platform_overview,
)
from .serializers import (
    PlatformOrganizationSerializer,
    PlatformProjectSerializer,
    PlatformUserSerializer,
)
from .utils import paginate, parse_pagination


def _pagination_schema(name: str):
    return inline_serializer(
        name,
        fields={
            "page": serializers.IntegerField(),
            "page_size": serializers.IntegerField(),
            "total": serializers.IntegerField(),
            "pages": serializers.IntegerField(),
            "has_next": serializers.BooleanField(),
            "has_previous": serializers.BooleanField(),
        },
    )


_OVERVIEW_PAYLOAD = {
    "users": 0,
    "organizations": 0,
    "projects": 0,
    "events_24h": 0,
    "open_incidents": 0,
    "open_incidents_by_severity": {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    },
    "ai_24h": {"pending": 0, "ready": 0, "failed": 0},
    "alerts_24h": 0,
    "top_projects": [
        {
            "id": "",
            "name": "",
            "organization": "",
            "events_24h": 0,
            "open_incidents": 0,
        }
    ],
    "recent_audit": [
        {
            "id": "",
            "action": "",
            "actor_email": "",
            "organization": "",
            "target": "",
            "created_at": "",
        }
    ],
}


class PlatformAdminMixin(APIView):
    """Shared gate: staff-only + per-user throttle on every admin view."""

    permission_classes = [IsStaff]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "platform_admin"


class PlatformOverviewView(PlatformAdminMixin):
    """GET /api/v1/admin/overview/ — high-signal platform counts."""

    @extend_schema(
        tags=["platform"],
        operation_id="platform_overview",
        summary="Platform overview",
        description=(
            "Site-wide totals (users, organizations, projects, 24h events, "
            "open incidents by severity, 24h AI/alert activity), top projects "
            "by 24h volume, and the latest audit entries. TTL-cached for 60s."
        ),
        responses={
            200: envelope_schema(
                "PlatformOverviewOk",
                payload={"overview": _OVERVIEW_PAYLOAD},
            ),
            401: envelope_schema("PlatformOverviewUnauthorized", error=True),
            403: envelope_schema("PlatformOverviewForbidden", error=True),
        },
    )
    def get(self, request):
        overview = cached_overview(platform_overview)
        return api_success({"overview": overview})


class PlatformUserListView(PlatformAdminMixin):
    """GET /api/v1/admin/users/ — every account, searchable, paginated."""

    @extend_schema(
        tags=["platform"],
        operation_id="platform_users",
        summary="List platform users",
        description=(
            "All accounts newest-first with org counts. Filter with "
            "?search=<email substring>. Never exposes password hashes."
        ),
        responses={
            200: envelope_schema(
                "PlatformUsersOk",
                payload=inline_serializer(
                    "PlatformUsersData",
                    fields={
                        "users": PlatformUserSerializer(many=True),
                        "pagination": _pagination_schema(
                            "PlatformUsersPagination"
                        ),
                    },
                ),
            ),
            401: envelope_schema("PlatformUsersUnauthorized", error=True),
            403: envelope_schema("PlatformUsersForbidden", error=True),
        },
    )
    def get(self, request):
        page, page_size = parse_pagination(request.query_params)
        users, pagination = paginate(
            list_users(search=request.query_params.get("search", "")),
            page,
            page_size,
        )
        return api_success(
            {
                "users": PlatformUserSerializer(users, many=True).data,
                "pagination": pagination,
            }
        )


class PlatformOrganizationListView(PlatformAdminMixin):
    """GET /api/v1/admin/organizations/ — every tenant, searchable."""

    @extend_schema(
        tags=["platform"],
        operation_id="platform_organizations",
        summary="List platform organizations",
        description=(
            "All tenants with owner email plus member/project counts. "
            "Filter with ?search=<name substring>."
        ),
        responses={
            200: envelope_schema(
                "PlatformOrganizationsOk",
                payload=inline_serializer(
                    "PlatformOrganizationsData",
                    fields={
                        "organizations": PlatformOrganizationSerializer(
                            many=True
                        ),
                        "pagination": _pagination_schema(
                            "PlatformOrganizationsPagination"
                        ),
                    },
                ),
            ),
            401: envelope_schema(
                "PlatformOrganizationsUnauthorized", error=True
            ),
            403: envelope_schema("PlatformOrganizationsForbidden", error=True),
        },
    )
    def get(self, request):
        page, page_size = parse_pagination(request.query_params)
        orgs, pagination = paginate(
            list_organizations(
                search=request.query_params.get("search", "")
            ),
            page,
            page_size,
        )
        return api_success(
            {
                "organizations": PlatformOrganizationSerializer(
                    orgs, many=True
                ).data,
                "pagination": pagination,
            }
        )


class PlatformProjectListView(PlatformAdminMixin):
    """GET /api/v1/admin/projects/ — every project, searchable."""

    @extend_schema(
        tags=["platform"],
        operation_id="platform_projects",
        summary="List platform projects",
        description=(
            "All projects with 24h event volume and open-incident counts. "
            "Only the key prefix is exposed, never the key hash. "
            "Filter with ?search=<name substring>."
        ),
        responses={
            200: envelope_schema(
                "PlatformProjectsOk",
                payload=inline_serializer(
                    "PlatformProjectsData",
                    fields={
                        "projects": PlatformProjectSerializer(many=True),
                        "pagination": _pagination_schema(
                            "PlatformProjectsPagination"
                        ),
                    },
                ),
            ),
            401: envelope_schema("PlatformProjectsUnauthorized", error=True),
            403: envelope_schema("PlatformProjectsForbidden", error=True),
        },
    )
    def get(self, request):
        page, page_size = parse_pagination(request.query_params)
        projects, pagination = paginate(
            list_projects(search=request.query_params.get("search", "")),
            page,
            page_size,
        )
        return api_success(
            {
                "projects": PlatformProjectSerializer(
                    projects, many=True
                ).data,
                "pagination": pagination,
            }
        )


class PlatformHealthView(PlatformAdminMixin):
    """GET /api/v1/admin/health/ — dependency checks + pipeline metrics.

    Same probes as the public ``/api/health/`` (database, cache, Celery
    worker — always reported, never failing the response), plus the 24h
    pipeline snapshot operators triage from.
    """

    @extend_schema(
        tags=["platform"],
        operation_id="platform_health",
        summary="Platform health",
        description=(
            "Dependency statuses (database, cache, worker) with latency plus "
            "24h ingest/AI/alert metrics. Always HTTP 200 — read ``status``."
        ),
        responses={
            200: envelope_schema(
                "PlatformHealthOk",
                payload={
                    "status": "",
                    "checks": {},
                    "metrics": {},
                },
            ),
            401: envelope_schema("PlatformHealthUnauthorized", error=True),
            403: envelope_schema("PlatformHealthForbidden", error=True),
        },
    )
    def get(self, request):
        checks = {
            "database": _check_database(),
            "cache": _check_cache(),
            "worker": _check_worker(),
        }
        try:
            metrics: dict = _pipeline_metrics()
        except Exception:  # noqa: BLE001 — metrics never fail the probe
            metrics = {}
        overall = "ok" if checks["database"]["status"] == "ok" else "degraded"
        return api_success(
            {"status": overall, "checks": checks, "metrics": metrics}
        )
