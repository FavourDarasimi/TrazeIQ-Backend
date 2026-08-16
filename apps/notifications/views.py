"""In-app notification inbox endpoints.

Everything is strictly self-scoped: a user can only ever read or mutate
their own notifications and preferences — there is nothing org-wide to
authorize, so ``IsAuthenticated`` is the whole permission story.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from trazeiq_backend.responses import api_success, envelope_schema

from .selectors import (
    get_alert_preferences,
    list_notifications_for_user,
    unread_count_for_user,
)
from .serializers import (
    AlertPreferenceInputSerializer,
    AlertPreferenceOutputSerializer,
    NotificationMarkReadInputSerializer,
    NotificationOutputSerializer,
)
from .services import mark_notifications_read


def _notification_list_schema(name: str):
    return inline_serializer(
        name,
        fields={
            "notifications": NotificationOutputSerializer(many=True),
            "unread_count": serializers.IntegerField(),
        },
    )


def _unread_schema(name: str):
    return inline_serializer(
        name,
        fields={"unread_count": serializers.IntegerField()},
    )


class NotificationListView(APIView):
    """GET /api/notifications/ — the caller's inbox, newest first, with the
    unread counter. Narrow with ``?limit=`` (default 50, max 200) and
    ``?offset=``."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["notifications"],
        operation_id="notifications_list",
        summary="List in-app notifications",
        description=(
            "The caller's notification inbox, newest first, plus the live "
            "unread count. Optional ``limit`` (default 50, max 200) and "
            "``offset`` window the list."
        ),
        parameters=[
            inline_serializer(
                "NotificationListQuery",
                fields={
                    "limit": serializers.IntegerField(required=False),
                    "offset": serializers.IntegerField(required=False),
                },
            )
        ],
        responses={
            200: envelope_schema(
                "NotificationListOk",
                payload=_notification_list_schema("NotificationListData"),
            ),
            401: envelope_schema("NotificationListUnauthorized", error=True),
        },
    )
    def get(self, request):
        raw_limit = request.query_params.get("limit", "50")
        raw_offset = request.query_params.get("offset", "0")
        try:
            limit = int(raw_limit)
            offset = int(raw_offset)
        except (TypeError, ValueError):
            raise serializers.ValidationError(
                {"limit": "Must be integers.", "offset": "Must be integers."}
            )
        if limit < 1 or offset < 0:
            raise serializers.ValidationError(
                {"limit": "Must be at least 1.", "offset": "Must be 0 or greater."}
            )
        notifications = list_notifications_for_user(
            request.user, limit=limit, offset=offset
        )
        return api_success(
            data={
                "notifications": NotificationOutputSerializer(
                    notifications, many=True
                ).data,
                "unread_count": unread_count_for_user(request.user),
            }
        )


class NotificationUnreadCountView(APIView):
    """GET /api/notifications/unread-count/ — cheap counter for the client's
    badge poll."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["notifications"],
        operation_id="notifications_unread_count",
        summary="Unread notification count",
        responses={
            200: envelope_schema(
                "NotificationUnreadCountOk",
                payload=_unread_schema("NotificationUnreadCountData"),
            ),
            401: envelope_schema("NotificationUnreadCountUnauthorized", error=True),
        },
    )
    def get(self, request):
        return api_success(data={"unread_count": unread_count_for_user(request.user)})


class NotificationMarkReadView(APIView):
    """POST /api/notifications/read/ — mark the given ids read, or every
    notification when ``ids`` is omitted/empty."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["notifications"],
        operation_id="notifications_mark_read",
        summary="Mark notifications as read",
        description=(
            "Mark the caller's notifications read. ``ids`` is a list of "
            "notification ids; when omitted or empty, every unread row is "
            "marked read at once."
        ),
        request=NotificationMarkReadInputSerializer,
        responses={
            200: envelope_schema(
                "NotificationMarkReadOk",
                payload=inline_serializer(
                    "NotificationMarkReadData",
                    fields={
                        "marked": serializers.IntegerField(),
                        "unread_count": serializers.IntegerField(),
                    },
                ),
            ),
            400: envelope_schema("NotificationMarkReadValidation", error=True),
            401: envelope_schema("NotificationMarkReadUnauthorized", error=True),
        },
    )
    def post(self, request):
        serializer = NotificationMarkReadInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        marked = mark_notifications_read(
            request.user, notification_ids=serializer.validated_data.get("ids")
        )
        return api_success(
            data={
                "marked": marked,
                "unread_count": unread_count_for_user(request.user),
            }
        )


class AlertPreferenceView(APIView):
    """GET /api/notifications/preferences/ — the caller's knobs (defaults
    materialize on first read). PATCH — flip any subset of the four toggles."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["notifications"],
        operation_id="alert_preferences_get",
        summary="Get alert preferences",
        responses={
            200: envelope_schema(
                "AlertPreferenceGetOk",
                payload=inline_serializer(
                    "AlertPreferenceData",
                    fields={"preferences": AlertPreferenceOutputSerializer()},
                ),
            ),
            401: envelope_schema("AlertPreferenceGetUnauthorized", error=True),
        },
    )
    def get(self, request):
        preferences = get_alert_preferences(request.user)
        return api_success(
            data={"preferences": AlertPreferenceOutputSerializer(preferences).data}
        )

    @extend_schema(
        tags=["notifications"],
        operation_id="alert_preferences_update",
        summary="Update alert preferences",
        request=AlertPreferenceInputSerializer,
        responses={
            200: envelope_schema(
                "AlertPreferenceUpdateOk",
                payload=inline_serializer(
                    "AlertPreferenceUpdateData",
                    fields={"preferences": AlertPreferenceOutputSerializer()},
                ),
            ),
            400: envelope_schema("AlertPreferenceUpdateValidation", error=True),
            401: envelope_schema("AlertPreferenceUpdateUnauthorized", error=True),
        },
    )
    def patch(self, request):
        preferences = get_alert_preferences(request.user)
        serializer = AlertPreferenceInputSerializer(
            preferences, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return api_success(
            data={"preferences": AlertPreferenceOutputSerializer(preferences).data}
        )


__all__ = [
    "AlertPreferenceView",
    "NotificationListView",
    "NotificationMarkReadView",
    "NotificationUnreadCountView",
]