"""Phase 2C: AI analysis manual trigger & retrieval endpoints.

- POST /api/v1/incidents/{id}/analyze/ — manually re-trigger analysis
- GET /api/v1/incidents/{id}/analysis/ — latest analysis state & result
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.incidents.selectors import get_incident_for_user
from trazeiq_backend.responses import api_success, envelope_schema

from .selectors import get_latest_analysis_for_incident
from .serializers import AIAnalysisOutputSerializer
from .services import trigger_manual_analysis

INCIDENT_NOT_FOUND = "This incident does not exist."
ANALYSIS_NOT_FOUND = "No analysis exists for this incident."


class IncidentAnalyzeView(APIView):
    """POST /api/v1/incidents/{id}/analyze/ — manually re-trigger root-cause
    analysis for an incident, bypassing the cache window."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["ai"],
        operation_id="incidents_analyze_create",
        summary="Re-analyze incident",
        description=(
            "Manually trigger a fresh AI root-cause analysis for an incident, "
            "bypassing the cache window. Returns the pending analysis object."
        ),
        request=None,
        responses={
            200: envelope_schema(
                "IncidentAnalyzeOk",
                payload=inline_serializer(
                    "IncidentAnalyzeData",
                    fields={"analysis": AIAnalysisOutputSerializer()},
                ),
            ),
            401: envelope_schema("IncidentAnalyzeUnauthorized", error=True),
            404: envelope_schema("IncidentAnalyzeNotFound", error=True),
        },
    )
    def post(self, request, incident_id: int):
        incident = get_incident_for_user(incident_id, request.user)
        if incident is None:
            raise NotFound(INCIDENT_NOT_FOUND)

        analysis = trigger_manual_analysis(incident=incident)
        return api_success(
            data={"analysis": AIAnalysisOutputSerializer(analysis).data}
        )


class IncidentAnalysisView(APIView):
    """GET /api/v1/incidents/{id}/analysis/ — fetch the latest AI analysis
    for an incident."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["ai"],
        operation_id="incidents_analysis_retrieve",
        summary="Retrieve incident analysis",
        description=(
            "Fetch the latest AI analysis for an incident. Returns a 404 "
            "error if no analysis has been requested or created yet."
        ),
        responses={
            200: envelope_schema(
                "IncidentAnalysisOk",
                payload=inline_serializer(
                    "IncidentAnalysisData",
                    fields={"analysis": AIAnalysisOutputSerializer()},
                ),
            ),
            401: envelope_schema("IncidentAnalysisUnauthorized", error=True),
            404: envelope_schema("IncidentAnalysisNotFound", error=True),
        },
    )
    def get(self, request, incident_id: int):
        incident = get_incident_for_user(incident_id, request.user)
        if incident is None:
            raise NotFound(INCIDENT_NOT_FOUND)

        analysis = get_latest_analysis_for_incident(incident_id)
        if analysis is None:
            raise NotFound(ANALYSIS_NOT_FOUND)

        return api_success(
            data={"analysis": AIAnalysisOutputSerializer(analysis).data}
        )


__all__ = [
    "IncidentAnalysisView",
    "IncidentAnalyzeView",
    "INCIDENT_NOT_FOUND",
    "ANALYSIS_NOT_FOUND",
]
