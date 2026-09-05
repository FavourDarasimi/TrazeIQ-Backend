"""SLO CRUD + dashboard endpoints.

Owner/admin manage SLO definitions (create/patch/delete); any org member
can list and read. The dependency-impact summary is keyed by ``project_id``
and requires the caller to see the project (cross-project attempts 404).
"""

from uuid import UUID

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.projects.models import Project
from apps.projects.selectors import get_project_for_user

from trazeiq_backend.responses import api_success, envelope_schema

from .models import SLO
from .permissions import IsSLOOwnerOrAdmin
from .selectors import (
    get_slo_for_user,
    list_alerts_for_slo,
    list_slos_for_user,
    list_snapshots_for_slo,
)
from .serializers import (
    SLOBreachAlertSerializer,
    SLOBudgetSnapshotSerializer,
    SLOInputSerializer,
    SLOOutputSerializer,
    SLODependencySummarySerializer,
)
from .services import (
    acknowledge_alert,
    evaluate_slo,
    maybe_fire_breach_alerts,
    project_summary,
)

SLO_NOT_FOUND = "This SLO does not exist."
PROJECT_NOT_FOUND = "This project does not exist."


def _slo_schema(name: str):
    return inline_serializer(name, fields={"slo": SLOOutputSerializer()})


def _snapshot_schema(name: str):
    return inline_serializer(
        name, fields={"snapshot": SLOBudgetSnapshotSerializer()}
    )


def _alerts_schema(name: str):
    return inline_serializer(
        name, fields={"alerts": SLOBreachAlertSerializer(many=True)}
    )


class SLOListView(APIView):
    """GET/POST /api/v1/slos/ — list/create SLOs in the caller's
    organizations.

    Create is owner/admin only; the project is read from the request body
    and resolved into the SLO via the view's ``get_permission_org_id``.
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            raw = self.request.data.get("project")
            if not raw:
                return [IsAuthenticated()]
            try:
                pid = UUID(str(raw))
            except (TypeError, ValueError, AttributeError):
                return [IsAuthenticated()]
            if not Project.objects.filter(id=pid).exists():
                return [IsAuthenticated()]
            return [IsAuthenticated(), IsSLOOwnerOrAdmin()]
        return super().get_permissions()

    def get_permission_org_id(self, request):
        raw = request.data.get("project")
        if not raw:
            return None
        try:
            project_id = UUID(str(raw))
        except (TypeError, ValueError, AttributeError):
            return None
        return (
            Project.objects.filter(id=project_id)
            .values_list("organization_id", flat=True)
            .first()
        )

    @extend_schema(
        tags=["slos"],
        operation_id="slos_list",
        summary="List SLOs",
        parameters=[
            inline_serializer(
                "SLOListQuery",
                fields={"project": serializers.UUIDField(required=False)},
            )
        ],
        responses={
            200: envelope_schema(
                "SLOListOk",
                payload=inline_serializer(
                    "SLOListData",
                    fields={"slos": SLOOutputSerializer(many=True)},
                ),
            ),
            401: envelope_schema("SLOListUnauthorized", error=True),
        },
    )
    def get(self, request):
        project_id_raw = request.query_params.get("project")
        project_id = None
        if project_id_raw:
            try:
                project_id = UUID(str(project_id_raw))
            except (TypeError, ValueError, AttributeError):
                raise serializers.ValidationError(
                    {"project": "Must be a valid UUID."}
                )
        slos = list_slos_for_user(request.user, project_id=project_id)
        return api_success(
            data={"slos": SLOOutputSerializer(slos, many=True).data}
        )

    @extend_schema(
        tags=["slos"],
        operation_id="slos_create",
        summary="Create an SLO",
        request=SLOInputSerializer,
        responses={
            201: envelope_schema("SLOCreateOk", payload=_slo_schema("SLOCreateData")),
            400: envelope_schema("SLOCreateValidation", error=True),
            401: envelope_schema("SLOCreateUnauthorized", error=True),
            403: envelope_schema("SLOCreateForbidden", error=True),
            404: envelope_schema("SLOCreateNotFound", error=True),
        },
    )
    def post(self, request):
        raw_project_id = request.data.get("project")
        if raw_project_id is None:
            raise serializers.ValidationError(
                {"project": "This field is required."}
            )
        try:
            project_id = UUID(str(raw_project_id))
        except (TypeError, ValueError, AttributeError):
            raise serializers.ValidationError(
                {"project": "Must be a valid UUID."}
            )
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)

        serializer = SLOInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slo = serializer.save(project=project)
        return api_success(
            data={"slo": SLOOutputSerializer(slo).data},
            status=status.HTTP_201_CREATED,
        )


class SLODetailView(APIView):
    """GET /api/v1/slos/{id}/ — retrieve. PATCH/DELETE — mutate (admin+)."""

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ("PATCH", "DELETE"):
            return [IsAuthenticated(), IsSLOOwnerOrAdmin()]
        return super().get_permissions()

    @extend_schema(
        tags=["slos"],
        operation_id="slos_retrieve",
        summary="Retrieve an SLO",
        responses={
            200: envelope_schema("SLORetrieveOk", payload=_slo_schema("SLORetrieveData")),
            401: envelope_schema("SLORetrieveUnauthorized", error=True),
            404: envelope_schema("SLORetrieveNotFound", error=True),
        },
    )
    def get(self, request, pk: UUID):
        slo = get_slo_for_user(pk, request.user)
        if slo is None:
            raise NotFound(SLO_NOT_FOUND)
        return api_success(data={"slo": SLOOutputSerializer(slo).data})

    @extend_schema(
        tags=["slos"],
        operation_id="slos_update",
        summary="Update an SLO",
        request=SLOInputSerializer,
        responses={
            200: envelope_schema("SLOUpdateOk", payload=_slo_schema("SLOUpdateData")),
            400: envelope_schema("SLOUpdateValidation", error=True),
            401: envelope_schema("SLOUpdateUnauthorized", error=True),
            403: envelope_schema("SLOUpdateForbidden", error=True),
            404: envelope_schema("SLOUpdateNotFound", error=True),
        },
    )
    def patch(self, request, pk: UUID):
        slo = get_slo_for_user(pk, request.user)
        if slo is None:
            raise NotFound(SLO_NOT_FOUND)
        # The project of an SLO cannot be moved — same contract as
        # ``AlertRule`` (4C): the row belongs to one project forever.
        if "project" in request.data:
            raise serializers.ValidationError(
                {"project": "The project of an SLO cannot be changed."}
            )
        serializer = SLOInputSerializer(
            slo, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        slo = serializer.save()
        return api_success(data={"slo": SLOOutputSerializer(slo).data})

    @extend_schema(
        tags=["slos"],
        operation_id="slos_delete",
        summary="Delete an SLO",
        responses={
            200: envelope_schema("SLODeleteOk"),
            401: envelope_schema("SLODeleteUnauthorized", error=True),
            403: envelope_schema("SLODeleteForbidden", error=True),
            404: envelope_schema("SLODeleteNotFound", error=True),
        },
    )
    def delete(self, request, pk: UUID):
        slo = get_slo_for_user(pk, request.user)
        if slo is None:
            raise NotFound(SLO_NOT_FOUND)
        slo.delete()
        return api_success(message="SLO deleted.")


class SLOBudgetView(APIView):
    """GET /api/v1/slos/{id}/budget/ — current snapshot + a fresh
    evaluation pass so the dashboard reflects events that landed since
    the last scheduled run."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["slos"],
        operation_id="slo_budget",
        summary="SLO budget snapshot",
        responses={
            200: envelope_schema(
                "SLOBudgetOk", payload=_snapshot_schema("SLOBudgetData")
            ),
            401: envelope_schema("SLOBudgetUnauthorized", error=True),
            404: envelope_schema("SLOBudgetNotFound", error=True),
        },
    )
    def get(self, request, pk: UUID):
        slo = get_slo_for_user(pk, request.user)
        if slo is None:
            raise NotFound(SLO_NOT_FOUND)
        snapshot = evaluate_slo(slo)
        if snapshot is None:
            # Disabled SLOs don't get new snapshots — return the latest
            # historical one if any, otherwise 404.
            from .selectors import latest_snapshot_for_slo

            snapshot = latest_snapshot_for_slo(slo)
            if snapshot is None:
                return api_success(data={"snapshot": None})
        maybe_fire_breach_alerts(snapshot)
        return api_success(
            data={"snapshot": SLOBudgetSnapshotSerializer(snapshot).data}
        )


class SLOHistoryView(APIView):
    """GET /api/v1/slos/{id}/history/ — snapshots in time order (oldest
    first), ready to drop into a chart. Default 100 points."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["slos"],
        operation_id="slo_history",
        summary="SLO budget history",
        parameters=[
            inline_serializer(
                "SLOHistoryQuery",
                fields={
                    "limit": serializers.IntegerField(
                        required=False, min_value=1, max_value=500
                    )
                },
            )
        ],
        responses={
            200: envelope_schema(
                "SLOHistoryOk",
                payload=inline_serializer(
                    "SLOHistoryData",
                    fields={"snapshots": SLOBudgetSnapshotSerializer(many=True)},
                ),
            ),
            401: envelope_schema("SLOHistoryUnauthorized", error=True),
            404: envelope_schema("SLOHistoryNotFound", error=True),
        },
    )
    def get(self, request, pk: UUID):
        slo = get_slo_for_user(pk, request.user)
        if slo is None:
            raise NotFound(SLO_NOT_FOUND)
        limit_raw = request.query_params.get("limit")
        limit = 100
        if limit_raw:
            try:
                limit = max(1, min(int(limit_raw), 500))
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    {"limit": "Must be a positive integer."}
                )
        # The selector returns newest-first; charts want oldest-first, so
        # reverse before serialization.
        snapshots = list(list_snapshots_for_slo(slo, limit=limit))[::-1]
        return api_success(
            data={
                "snapshots": SLOBudgetSnapshotSerializer(snapshots, many=True).data
            }
        )


class SLOAlertsView(APIView):
    """GET /api/v1/slos/{id}/alerts/ — recent breach alerts (newest first).
    POST /api/v1/slos/{id}/alerts/{alert_id}/acknowledge/ — mark one
    acknowledged (called from the list view's row action)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["slos"],
        operation_id="slo_alerts_list",
        summary="List breach alerts for one SLO",
        responses={
            200: envelope_schema("SLOAlertsOk", payload=_alerts_schema("SLOAlertsData")),
            401: envelope_schema("SLOAlertsUnauthorized", error=True),
            404: envelope_schema("SLOAlertsNotFound", error=True),
        },
    )
    def get(self, request, pk: UUID):
        slo = get_slo_for_user(pk, request.user)
        if slo is None:
            raise NotFound(SLO_NOT_FOUND)
        alerts = list_alerts_for_slo(slo)
        return api_success(
            data={"alerts": SLOBreachAlertSerializer(alerts, many=True).data}
        )


class SLOAlertAcknowledgeView(APIView):
    """POST /api/v1/slos/{slo_id}/alerts/{alert_id}/acknowledge/."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["slos"],
        operation_id="slo_alert_acknowledge",
        summary="Acknowledge a breach alert",
        request=None,
        responses={
            200: envelope_schema(
                "SLOAlertAckOk",
                payload=inline_serializer(
                    "SLOAlertAckData",
                    fields={"alert": SLOBreachAlertSerializer()},
                ),
            ),
            401: envelope_schema("SLOAlertAckUnauthorized", error=True),
            404: envelope_schema("SLOAlertAckNotFound", error=True),
        },
    )
    def post(self, request, pk: UUID, alert_id: UUID):
        slo = get_slo_for_user(pk, request.user)
        if slo is None:
            raise NotFound(SLO_NOT_FOUND)
        from .models import SLOBreachAlert

        alert = (
            SLOBreachAlert.objects.filter(id=alert_id, slo=slo).first()
        )
        if alert is None:
            raise NotFound("This alert does not exist.")
        alert = acknowledge_alert(alert, actor=request.user)
        return api_success(
            data={"alert": SLOBreachAlertSerializer(alert).data}
        )


class SLODependencyView(APIView):
    """GET /api/v1/slos/dependencies/?project=... — per-project
    dependency-impact summary (which SLOs are healthy / breached /
    exhausted, plus the burn-rate headlines)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["slos"],
        operation_id="slo_dependencies",
        summary="Service dependency impact",
        parameters=[
            inline_serializer(
                "SLODependenciesQuery",
                fields={"project": serializers.UUIDField(required=True)},
            )
        ],
        responses={
            200: envelope_schema(
                "SLODependenciesOk",
                payload=inline_serializer(
                    "SLODependenciesData",
                    fields={"summary": SLODependencySummarySerializer()},
                ),
            ),
            400: envelope_schema("SLODependenciesValidation", error=True),
            401: envelope_schema("SLODependenciesUnauthorized", error=True),
            404: envelope_schema("SLODependenciesNotFound", error=True),
        },
    )
    def get(self, request):
        raw_project = request.query_params.get("project")
        if not raw_project:
            raise serializers.ValidationError(
                {"project": "This field is required."}
            )
        try:
            project_id = UUID(str(raw_project))
        except (TypeError, ValueError, AttributeError):
            raise serializers.ValidationError(
                {"project": "Must be a valid UUID."}
            )
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)
        summary = project_summary(project.id)
        return api_success(data={"summary": summary})


__all__ = [
    "PROJECT_NOT_FOUND",
    "SLOAlertAcknowledgeView",
    "SLOAlertsView",
    "SLOBudgetView",
    "SLODependencyView",
    "SLODetailView",
    "SLOHistoryView",
    "SLOListView",
    "SLO_NOT_FOUND",
]