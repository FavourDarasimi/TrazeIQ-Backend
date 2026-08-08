"""Phase 3A: Pusher private-channel auth endpoint.

The browser subscribes to ``private-project-{id}`` and must prove access to
that project before Pusher lets the connection through. This endpoint checks
the caller's membership the same way every other tenant-scoped view does
(via the projects selector), then returns the signed auth response. The
Pusher secret never leaves the server.
"""

import re
from uuid import UUID

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.projects.selectors import get_project_for_user
from trazeiq_backend.responses import api_error, api_success, envelope_schema

from .pusher import PusherUnavailable, authenticate_channel

CHANNEL_NAME_RE = re.compile(r"^private-project-(?P<project_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.IGNORECASE)


class PusherAuthView(APIView):
    """POST /api/pusher/auth/ — sign a private-project channel for a member.

    Accepts ``channel_name`` (``private-project-{uuid}``) and ``socket_id``
    (from pusher-js), verifies the caller is a member of the project encoded
    in the channel, and returns the signed auth payload.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["realtime"],
        operation_id="pusher_authenticate",
        summary="Authorize a Pusher private channel",
        request=inline_serializer(
            "PusherAuthRequest",
            fields={
                "channel_name": serializers.CharField(),
                "socket_id": serializers.CharField(),
            },
        ),
        responses={
            200: envelope_schema(
                "PusherAuthOk",
                payload=inline_serializer(
                    "PusherAuthData",
                    fields={"auth": serializers.CharField()},
                ),
            ),
            400: envelope_schema("PusherAuthValidation", error=True),
            401: envelope_schema("PusherAuthUnauthorized", error=True),
            403: envelope_schema("PusherAuthForbidden", error=True),
        },
    )
    def post(self, request):
        channel_name = request.data.get("channel_name")
        socket_id = request.data.get("socket_id")

        missing = {
            field: ["This field is required."]
            for field, value in (("channel_name", channel_name), ("socket_id", socket_id))
            if not value
        }
        if missing:
            raise ValidationError(missing)

        match = CHANNEL_NAME_RE.fullmatch(channel_name)
        if match is None:
            raise PermissionDenied("Unknown private channel.")
        project_id = UUID(match.group("project_id"))

        if get_project_for_user(project_id, request.user) is None:
            raise PermissionDenied(
                "You do not have access to this project's channel."
            )

        try:
            auth = authenticate_channel(
                channel_name=channel_name, socket_id=socket_id
            )
        except PusherUnavailable:
            return api_error(
                "PUSHER_NOT_CONFIGURED",
                "Realtime is not configured on this server.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return api_success(data=auth)
