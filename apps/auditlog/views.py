from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.organizations.models import MembershipRole
from apps.organizations.permissions import get_membership_role, role_at_least
from trazeiq_backend.responses import api_success

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
