from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.organizations.models import MembershipRole
from apps.organizations.permissions import get_membership_role, role_at_least
from trazeiq_backend.responses import api_success, envelope_schema

from .permissions import IsOrgOwnerOrAdminAny
from .selectors import list_audit_logs_for_user
from .serializers import AuditLogOutputSerializer


class AuditLogListView(APIView):
    """GET /api/v1/audit-logs/ — tenant audit trail (owner/admin only).

    A developer (no admin org) is rejected with 403. Owners/admins see the
    audit logs across the orgs they administer, or a single org when the
    ``organization`` query parameter is supplied (access-checked).
    """

    permission_classes = [IsAuthenticated, IsOrgOwnerOrAdminAny]
    serializer_class = AuditLogOutputSerializer

    @extend_schema(
        tags=["organizations"],
        operation_id="audit_logs_list",
        summary="List audit logs",
        responses={
            200: envelope_schema(
                "AuditLogListOk",
                payload=inline_serializer(
                    "AuditLogListData",
                    fields={
                        "audit_logs": AuditLogOutputSerializer(many=True)
                    },
                ),
            ),
            401: envelope_schema("AuditLogListUnauthorized", error=True),
            403: envelope_schema("AuditLogListForbidden", error=True),
        },
    )
    def get(self, request):
        organization_id = request.query_params.get("organization")
        if organization_id is not None:
            if not role_at_least(
                get_membership_role(request.user, organization_id),
                MembershipRole.ADMIN,
            ):
                raise PermissionDenied("Not permitted for this organization.")
        logs = list_audit_logs_for_user(request.user, organization_id)
        return api_success(
            data={
                "audit_logs": AuditLogOutputSerializer(logs, many=True).data
            }
        )
