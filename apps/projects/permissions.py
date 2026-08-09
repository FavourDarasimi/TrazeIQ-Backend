"""Project-scoped RBAC (Phase 4A) — resolves the target organization from
the project in the URL instead of the URL's own ``pk``."""

from uuid import UUID

from apps.organizations.models import MembershipRole
from apps.organizations.permissions import OrgRolePermission

from .models import Project


class ProjectRolePermission(OrgRolePermission):
    def get_org_id_from_view(self, request, view) -> UUID | None:
        project_id = view.kwargs.get("pk")
        if project_id is None:
            return None
        return (
            Project.objects.filter(id=project_id)
            .values_list("organization_id", flat=True)
            .first()
        )


class IsProjectMember(ProjectRolePermission):
    minimum_role = MembershipRole.VIEWER


class IsProjectOwnerOrAdmin(ProjectRolePermission):
    """Project management: create/update/delete, API-key rotation."""

    minimum_role = MembershipRole.ADMIN
