from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView
from uuid import UUID

import math

from trazeiq_backend.responses import api_success, api_error, envelope_schema

from .authentication import APIKeyAuthentication
from .permissions import IsAPIKeyAuthenticated
from .serializers import EventInputSerializer, EventOutputSerializer
from .selectors import get_event_for_user, list_events_for_user, parse_date_filter
from .services import ingest_event
from .throttles import EventPerKeyRateThrottle, EventPerIpRateThrottle

EVENT_NOT_FOUND = "This event does not exist."

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def _parse_pagination(query) -> tuple[int, int]:
    """Parse ``page``/``page_size``, raising DRF validation on bad values."""
    errors: dict[str, str] = {}
    try:
        page = int(query.get("page", "1"))
        if page < 1:
            errors["page"] = "Must be a positive integer."
    except (TypeError, ValueError):
        errors["page"] = "Must be a positive integer."
    try:
        page_size = int(query.get("page_size", str(DEFAULT_PAGE_SIZE)))
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            errors["page_size"] = (
                f"Must be an integer between 1 and {MAX_PAGE_SIZE}."
            )
    except (TypeError, ValueError):
        errors["page_size"] = f"Must be an integer between 1 and {MAX_PAGE_SIZE}."
    if errors:
        raise serializers.ValidationError(errors)
    return page, page_size


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
            return [EventPerIpRateThrottle(), EventPerKeyRateThrottle()]
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
            "Events from every project in the caller's organizations, "
            "newest first. Filter by level, environment, service, ISO date "
            "or a free-text search (message, fingerprint, service, "
            "environment, endpoint), and paginate with page / page_size."
        ),
        parameters=[
            inline_serializer(
                "EventListQuery",
                fields={
                    "level": serializers.CharField(required=False),
                    "environment": serializers.CharField(required=False),
                    "service": serializers.CharField(required=False),
                    "date": serializers.DateField(required=False),
                    "search": serializers.CharField(required=False),
                    "page": serializers.IntegerField(required=False),
                    "page_size": serializers.IntegerField(required=False),
                },
            )
        ],
        responses={
            200: envelope_schema(
                "EventListOk",
                payload=inline_serializer(
                    "EventListData",
                    fields={
                        "events": EventOutputSerializer(many=True),
                        "pagination": inline_serializer(
                            "EventListPagination",
                            fields={
                                "page": serializers.IntegerField(),
                                "page_size": serializers.IntegerField(),
                                "total": serializers.IntegerField(),
                                "pages": serializers.IntegerField(),
                                "has_next": serializers.BooleanField(),
                                "has_previous": serializers.BooleanField(),
                            },
                        ),
                    },
                ),
            ),
            400: envelope_schema("EventListValidation", error=True),
            401: envelope_schema("EventListUnauthorized", error=True),
        },
    )
    def get(self, request):
        query = request.query_params
        try:
            date_filter = (
                parse_date_filter(query["date"]) if query.get("date") else None
            )
        except ValueError:
            return api_error(
                "INVALID_DATE", "date must be YYYY-MM-DD.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        page, page_size = _parse_pagination(query)

        events = list_events_for_user(
            request.user,
            level=query.get("level"),
            environment=query.get("environment"),
            service=query.get("service"),
            date=date_filter,
            search=query.get("search"),
        )
        total = events.count()
        start = (page - 1) * page_size
        page_events = events[start : start + page_size]
        serializer = EventOutputSerializer(page_events, many=True)
        return api_success(
            {
                "events": serializer.data,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "pages": math.ceil(total / page_size),
                    "has_next": start + page_size < total,
                    "has_previous": page > 1,
                },
            }
        )


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
    def get(self, request, event_id: UUID):
        event = get_event_for_user(event_id, request.user)
        if event is None:
            raise NotFound(EVENT_NOT_FOUND)
        return api_success(data={"event": EventOutputSerializer(event).data})
