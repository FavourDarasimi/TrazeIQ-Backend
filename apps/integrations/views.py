"""Phase 4D: Slack integration endpoints.

``POST /api/v1/integrations/slack/connect/`` exchanges an OAuth code for an
access token and stores it encrypted at rest (owner/admin only);
``GET .../status/`` tells any member whether the org's workspace is
connected. Mirrors the Pusher convention: missing app credentials surface
as 503 ``SLACK_NOT_CONFIGURED``.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from uuid import UUID

from apps.organizations.selectors import (
    get_organization_for_user,
    list_organizations_for_user,
)
from apps.organizations.permissions import IsOrgOwnerOrAdmin

from trazeiq_backend.responses import api_error, api_success, envelope_schema

from .models import SlackIntegration
from .slack import SlackAPIError, SlackUnavailable, exchange_oauth_code

ORGANIZATION_NOT_FOUND = "This organization does not exist."


class SlackConnectView(APIView):
    """POST /api/v1/integrations/slack/connect/ — OAuth code exchange.

    The caller passes the code Slack redirected to (frontend performs the
    browser dance); the server exchanges it for a token and stores the token
    encrypted at rest. Reconnecting replaces the stored token.
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrgOwnerOrAdmin()]
        return super().get_permissions()

    def get_permission_org_id(self, request):
        """The target org for the connect permission — the body's
        ``organization``, falling back to the caller's first org (mirroring
        project creation) so validation errors still surface as 400s for
        members."""
        raw = request.data.get("organization")
        if raw:
            try:
                return UUID(str(raw))
            except (TypeError, ValueError, AttributeError):
                return None
        organization = list_organizations_for_user(request.user).first()
        return organization.id if organization else None

    @extend_schema(
        tags=["integrations"],
        operation_id="slack_connect",
        summary="Connect a Slack workspace",
        description=(
            "Exchange an OAuth code (from the Slack browser flow) for a "
            "bot token, stored encrypted at rest. Owner/admin only. "
            "Reconnecting replaces the previous token."
        ),
        request=inline_serializer(
            "SlackConnectInput",
            fields={
                "organization": serializers.UUIDField(),
                "code": serializers.CharField(),
                "redirect_uri": serializers.CharField(required=False),
            },
        ),
        responses={
            200: envelope_schema(
                "SlackConnectOk",
                payload=inline_serializer(
                    "SlackConnectData",
                    fields={
                        "connected": serializers.BooleanField(),
                        "team_name": serializers.CharField(required=False),
                    },
                ),
            ),
            400: envelope_schema("SlackConnectValidation", error=True),
            401: envelope_schema("SlackConnectUnauthorized", error=True),
            403: envelope_schema("SlackConnectForbidden", error=True),
            404: envelope_schema("SlackConnectNotFound", error=True),
            503: envelope_schema("SlackConnectUnavailable", error=True),
        },
    )
    def post(self, request):
        missing = {
            field: "This field is required."
            for field in ("organization", "code")
            if request.data.get(field) is None
        }
        if missing:
            raise serializers.ValidationError(missing)

        organization_id = request.data["organization"]
        code = request.data["code"]
        try:
            organization = get_organization_for_user(
                UUID(str(organization_id)), request.user
            )
        except (TypeError, ValueError, AttributeError):
            raise serializers.ValidationError(
                {"organization": "Must be a valid UUID."}
            )
        if organization is None:
            raise NotFound(ORGANIZATION_NOT_FOUND)

        try:
            token = exchange_oauth_code(
                code, redirect_uri=request.data.get("redirect_uri")
            )
        except SlackUnavailable:
            return api_error(
                "SLACK_NOT_CONFIGURED",
                "Slack integration is not configured on this server.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except SlackAPIError as exc:
            return api_error(
                "SLACK_CONNECT_FAILED",
                f"Slack rejected the connection: {exc}",
                status=status.HTTP_400_BAD_REQUEST,
            )

        integration, _created = SlackIntegration.objects.update_or_create(
            organization=organization,
            defaults={
                "access_token": token["access_token"],
                "team_name": token["team_name"],
            },
        )
        return api_success(
            data={
                "connected": True,
                "team_name": integration.team_name,
            }
        )


class SlackStatusView(APIView):
    """GET /api/v1/integrations/slack/status/?organization= — is the org's
    workspace connected? Any member may read; foreign orgs 404."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["integrations"],
        operation_id="slack_status",
        summary="Slack connection status",
        parameters=[
            inline_serializer(
                "SlackStatusQuery",
                fields={"organization": serializers.UUIDField()},
            )
        ],
        responses={
            200: envelope_schema(
                "SlackStatusOk",
                payload=inline_serializer(
                    "SlackStatusData",
                    fields={
                        "connected": serializers.BooleanField(),
                        "team_name": serializers.CharField(required=False),
                    },
                ),
            ),
            401: envelope_schema("SlackStatusUnauthorized", error=True),
            404: envelope_schema("SlackStatusNotFound", error=True),
        },
    )
    def get(self, request):
        raw = request.query_params.get("organization")
        try:
            organization_id = UUID(str(raw))
        except (TypeError, ValueError, AttributeError):
            raise serializers.ValidationError(
                {"organization": "Must be a valid UUID."}
            )
        organization = get_organization_for_user(organization_id, request.user)
        if organization is None:
            raise NotFound(ORGANIZATION_NOT_FOUND)

        integration = SlackIntegration.objects.filter(
            organization=organization
        ).first()
        return api_success(
            data={
                "connected": integration is not None,
                "team_name": integration.team_name if integration else None,
            }
        )


__all__ = ["SlackConnectView", "SlackStatusView", "ORGANIZATION_NOT_FOUND"]