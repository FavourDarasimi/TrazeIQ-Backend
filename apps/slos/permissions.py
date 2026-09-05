"""SLO-scoped RBAC — SLO management is owner/admin only (Phase 4A's project
management tier), reads are member-scoped with the no-existence-leak
contract used everywhere else."""

from uuid import UUID

from apps.organizations.models import MembershipRole
from apps.organizations.permissions import OrgRolePermission

from .models import SLO


class IsSLOOwnerOrAdmin(OrgRolePermission):
    """Owner/admin only: create/update/delete SLOs.

    Resolves the org from the target SLO (``pk`` URL kwarg) on PATCH/DELETE.
    For create the view's ``get_permission_org_id`` resolves it from the
    request body. Non-members pass through so the tenant getter 404s.
    """

    minimum_role = MembershipRole.ADMIN

    def get_org_id_from_view(self, request, view) -> UUID | None:
        slo_id = view.kwargs.get("pk")
        if slo_id is None:
            return None
        return (
            SLO.objects.filter(id=slo_id)
            .values_list("project__organization_id", flat=True)
            .first()
        )