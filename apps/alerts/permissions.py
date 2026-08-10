"""Alert-scoped RBAC (Phase 4C) — rule management sits in the project
management tier (owner/admin), like API-key rotation."""

from uuid import UUID

from apps.organizations.models import MembershipRole
from apps.organizations.permissions import OrgRolePermission

from .models import AlertRule


class IsAlertRuleOwnerOrAdmin(OrgRolePermission):
    """Owner/admin only: create/update/delete alert rules.

    Resolves the org from the target rule's project (``pk`` URL kwarg); for
    creation the view's ``get_permission_org_id`` resolves it from the
    project id in the request body. Non-members pass through so the view's
    tenant getter 404s (no existence leak).
    """

    minimum_role = MembershipRole.ADMIN

    def get_org_id_from_view(self, request, view) -> UUID | None:
        rule_id = view.kwargs.get("pk")
        if rule_id is None:
            return None
        return (
            AlertRule.objects.filter(id=rule_id)
            .values_list("project__organization_id", flat=True)
            .first()
        )