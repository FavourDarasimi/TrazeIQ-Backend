from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from trazeiq_backend.responses import api_success, api_error, envelope_schema

from .authentication import APIKeyAuthentication
from .permissions import IsAPIKeyAuthenticated
from .serializers import EventInputSerializer, EventOutputSerializer
from .selectors import get_event_for_user, list_events_for_user, parse_date_filter
from .services import ingest_event
from .throttles import EventPerKeyRateThrottle, EventScopedRateThrottle

EVENT_NOT_FOUND = "This event does not exist."


def _event_schema(name: str):
    return inline_serializer(
        name,
        fields={"event": EventOutputSerializer()},
    )


class EventIngestAndListView(APIView):
    """POST /api/v1/events/ (ingest) and GET /api/v1/events/ (list).

    Two surfaces, one URL. POST is the monitored app's one-call direct HTTP
    ingestion (X-API-Key, the caller is a Project); GET is the human-facing,
    org-scoped listing (JWT, the caller is a User). Auth classes therefore
    depend on the method.
    """

    throttle_scope = "IP"

    def _is_ingest(self):
        # drf-spectacular inspects views with a mock request; fall back to the
        # POST path (the primary surface) when no real method is available.
        return getattr(self.request, "method", "POST") == "POST"

    def get_authenticators(self):
        if self._is_ingest():
            return [APIKeyAuthentication()]
        return super().get_authenticators()

    def get_permissions(self):
        if self._is_ingest():
            return [IsAPIKeyAuthenticated()]
        return super().get_permissions()

    def get_throttles(self):
        if self._is_ingest():
            return [EventScopedRateThrottle(), EventPerKeyRateThrottle()]
        return super().get_throttles()

    @extend_schema(
        tags=["events"],
        operation_id="event_ingest",
        summary="Ingest one error event",
        description=(
            "The monitored app's single call: persist an error occurrence, "
            "deduplicate it into its ErrorGroup, and keep the open Incident "
            "current. Secrets in the message/stacktrace are redacted before "
            "storage."
        ),
        request=EventInputSerializer,
        responses={
            201: envelope_schema(
                "EventIngestOk", payload=_event_schema("EventIngestData")
            ),
            400: envelope_schema("EventIngestValidation", error=True),
            401: envelope_schema("EventIngestUnauthorized", error=True),
            413: envelope_schema("EventIngestTooLarge", error=True),
            429: envelope_schema("EventIngestThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = EventInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = ingest_event(request.user, **serializer.validated_data)
        return api_success(
            data={"event": EventOutputSerializer(event).data},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["events"],
        operation_id="events_list",
        summary="List events",
        description=(
            "Events from every project in the caller's organizations. "
            "Filter by level, environment, service or ISO date."
        ),
        parameters=[
            inline_serializer(
                "EventListQuery",
                fields={
                    "level": serializers.CharField(required=False),
                    "environment": serializers.CharField(required=False),
                    "service": serializers.CharField(required=False),
                    "date": serializers.DateField(required=False),
                },
            )
        ],
        responses={
            200: envelope_schema(
                "EventListOk",
                payload=inline_serializer(
                    "EventListData",
                    fields={"events": EventOutputSerializer(many=True)},
                ),
            ),
            400: envelope_schema("EventListValidation", error=True),
            401: envelope_schema("EventListUnauthorized", error=True),
        },
    )
    def get(self, request):
        try:
            date_filter = (
                parse_date_filter(request.query_params["date"])
                if request.query_params.get("date")
                else None
            )
        except ValueError:
            return api_error(
                "INVALID_DATE", "date must be YYYY-MM-DD.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        events = list_events_for_user(
            request.user,
            level=request.query_params.get("level"),
            environment=request.query_params.get("environment"),
            service=request.query_params.get("service"),
            date=date_filter,
        )
        serializer = EventOutputSerializer(events, many=True)
        return api_success({"events": serializer.data})


class EventDetailView(APIView):
    """GET /api/v1/events/{id}/ — single event, 404 if not in the user's org."""

    @extend_schema(
        tags=["events"],
        operation_id="event_detail",
        summary="Get one event",
        responses={
            200: envelope_schema(
                "EventDetailOk", payload=_event_schema("EventDetailData")
            ),
            401: envelope_schema("EventDetailUnauthorized", error=True),
            404: envelope_schema("EventDetailNotFound", error=True),
        },
    )
    def get(self, request, event_id: int):
        event = get_event_for_user(event_id, request.user)
        if event is None:
            raise NotFound(EVENT_NOT_FOUND)
        return api_success(data={"event": EventOutputSerializer(event).data})
