"""Phase 4C: alert rule and log endpoints.

Rule management (create/update/delete) is owner/admin project management;
reads are available to any member, scoped the usual way (unknown and
foreign ids 404).
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from uuid import UUID

from apps.projects.models import Project
from apps.projects.selectors import get_project_for_user

from trazeiq_backend.responses import api_success, envelope_schema

from .permissions import IsAlertRuleOwnerOrAdmin
from .selectors import (
    get_rule_for_user,
    list_logs_for_user,
    list_rules_for_user,
)
from .serializers import (
    AlertLogOutputSerializer,
    AlertRuleInputSerializer,
    AlertRuleOutputSerializer,
)

RULE_NOT_FOUND = "This alert rule does not exist."
PROJECT_NOT_FOUND = "This project does not exist."


def _rule_schema(name: str):
    return inline_serializer(
        name,
        fields={"rule": AlertRuleOutputSerializer()},
    )


class AlertRuleListView(APIView):
    """GET /api/alerts/rules/ — rules in the caller's organizations.
    POST /api/alerts/rules/ — create a rule for one of the caller's
    projects (owner/admin)."""

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # Creating rules is project management — owner/admin only. The
        # target project is picked in the request body, so the permission
        # resolves it via get_permission_org_id.
        if self.request.method == "POST":
            return [IsAuthenticated(), IsAlertRuleOwnerOrAdmin()]
        return super().get_permissions()

    def get_permission_org_id(self, request):
        """The target org for the create permission: the org of the project
        picked in the request body (mirroring the create logic, so
        permission and view always agree)."""
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
        tags=["alerts"],
        operation_id="alert_rules_list",
        summary="List alert rules",
        description=(
            "Alert rules in the caller's organizations, newest first. "
            "Narrow to one project with ``?project=``."
        ),
        parameters=[
            inline_serializer(
                "AlertRuleListQuery",
                fields={"project": serializers.UUIDField(required=False)},
            )
        ],
        responses={
            200: envelope_schema(
                "AlertRuleListOk",
                payload=inline_serializer(
                    "AlertRuleListData",
                    fields={"rules": AlertRuleOutputSerializer(many=True)},
                ),
            ),
            401: envelope_schema("AlertRuleListUnauthorized", error=True),
        },
    )
    def get(self, request):
        project_id = request.query_params.get("project")
        rules = list_rules_for_user(request.user, project_id=project_id)
        return api_success(
            data={"rules": AlertRuleOutputSerializer(rules, many=True).data}
        )

    @extend_schema(
        tags=["alerts"],
        operation_id="alert_rules_create",
        summary="Create an alert rule",
        description=(
            "Create a rule for one of the caller's projects (owner/admin). "
            "``condition`` is a small JSON object matching on incident "
            "severity and/or status, e.g. ``{\"severity\": \"critical\"}``. "
            "``cooldown_minutes`` (default 15) suppresses repeat dispatches "
            "for the same incident."
        ),
        request=AlertRuleInputSerializer,
        responses={
            201: envelope_schema(
                "AlertRuleCreateOk",
                payload=_rule_schema("AlertRuleCreateData"),
            ),
            400: envelope_schema("AlertRuleCreateValidation", error=True),
            401: envelope_schema("AlertRuleCreateUnauthorized", error=True),
            403: envelope_schema("AlertRuleCreateForbidden", error=True),
            404: envelope_schema("AlertRuleCreateNotFound", error=True),
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

        serializer = AlertRuleInputSerializer(
            data=request.data,
            context={"organization_id": project.organization_id},
        )
        serializer.is_valid(raise_exception=True)
        rule = serializer.save(project=project)
        return api_success(
            data={"rule": AlertRuleOutputSerializer(rule).data},
            status=status.HTTP_201_CREATED,
        )


class AlertRuleDetailView(APIView):
    """PATCH /api/alerts/rules/{id}/ — edit a rule (owner/admin).
    DELETE /api/alerts/rules/{id}/ — remove a rule (owner/admin)."""

    permission_classes = [IsAuthenticated, IsAlertRuleOwnerOrAdmin]

    @extend_schema(
        tags=["alerts"],
        operation_id="alert_rules_update",
        summary="Update an alert rule",
        description=(
            "Update the rule's name, condition, channel, target or cooldown "
            "(owner/admin). The project a rule belongs to cannot be changed."
        ),
        request=AlertRuleInputSerializer,
        responses={
            200: envelope_schema(
                "AlertRuleUpdateOk",
                payload=_rule_schema("AlertRuleUpdateData"),
            ),
            400: envelope_schema("AlertRuleUpdateValidation", error=True),
            401: envelope_schema("AlertRuleUpdateUnauthorized", error=True),
            403: envelope_schema("AlertRuleUpdateForbidden", error=True),
            404: envelope_schema("AlertRuleUpdateNotFound", error=True),
        },
    )
    def patch(self, request, pk: UUID):
        rule = get_rule_for_user(pk, request.user)
        if rule is None:
            raise NotFound(RULE_NOT_FOUND)

        # project is write-only on the serializer for create; on update it
        # must stay absent so the rule can never be moved to another project.
        if "project" in request.data:
            raise serializers.ValidationError(
                {"project": "The project of an alert rule cannot be changed."}
            )
        serializer = AlertRuleInputSerializer(
            rule,
            data=request.data,
            partial=True,
            context={"organization_id": rule.project.organization_id},
        )
        serializer.is_valid(raise_exception=True)
        rule = serializer.save()
        return api_success(data={"rule": AlertRuleOutputSerializer(rule).data})

    @extend_schema(
        tags=["alerts"],
        operation_id="alert_rules_delete",
        summary="Delete an alert rule",
        responses={
            200: envelope_schema("AlertRuleDeleteOk", error=False),
            401: envelope_schema("AlertRuleDeleteUnauthorized", error=True),
            403: envelope_schema("AlertRuleDeleteForbidden", error=True),
            404: envelope_schema("AlertRuleDeleteNotFound", error=True),
        },
    )
    def delete(self, request, pk: UUID):
        rule = get_rule_for_user(pk, request.user)
        if rule is None:
            raise NotFound(RULE_NOT_FOUND)
        rule.delete()
        return api_success(message="Alert rule deleted.")


class AlertLogListView(APIView):
    """GET /api/alerts/logs/ — dispatch logs in the caller's organizations,
    newest first. Narrow with ``?rule=`` and/or ``?incident=``."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["alerts"],
        operation_id="alert_logs_list",
        summary="List alert dispatch logs",
        description=(
            "Every dispatch attempt recorded for the caller's "
            "organizations, newest first. Optional ``rule`` and "
            "``incident`` filters narrow the list (foreign ids simply "
            "return an empty list)."
        ),
        parameters=[
            inline_serializer(
                "AlertLogListQuery",
                fields={
                    "rule": serializers.UUIDField(required=False),
                    "incident": serializers.UUIDField(required=False),
                },
            )
        ],
        responses={
            200: envelope_schema(
                "AlertLogListOk",
                payload=inline_serializer(
                    "AlertLogListData",
                    fields={
                        "logs": AlertLogOutputSerializer(many=True)
                    },
                ),
            ),
            401: envelope_schema("AlertLogListUnauthorized", error=True),
        },
    )
    def get(self, request):
        logs = list_logs_for_user(
            request.user,
            rule_id=request.query_params.get("rule"),
            incident_id=request.query_params.get("incident"),
        )
        return api_success(
            data={"logs": AlertLogOutputSerializer(logs, many=True).data}
        )


__all__ = [
    "AlertLogListView",
    "AlertRuleDetailView",
    "AlertRuleListView",
    "PROJECT_NOT_FOUND",
    "RULE_NOT_FOUND",
]