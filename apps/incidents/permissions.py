"""Incident-scoped RBAC (Phase 4A) — resolves the target organization from
the incident in the URL (``incident_id`` kwarg)."""

from uuid import UUID

from apps.organizations.models import MembershipRole
from apps.organizations.permissions import OrgRolePermission

from .models import Incident


class IncidentRolePermission(OrgRolePermission):
    def get_org_id_from_view(self, request, view) -> UUID | None:
        incident_id = view.kwargs.get("incident_id")
        if incident_id is None:
            return None
        return (
            Incident.objects.filter(id=incident_id)
            .values_list("project__organization_id", flat=True)
            .first()
        )


class IsIncidentDeveloperOrAbove(IncidentRolePermission):
    """Incident workflow: resolve, update status/severity/assignment, comment,
    re-trigger AI analysis. Viewers are read-only."""

    minimum_role = MembershipRole.DEVELOPER


class IsBulkIncidentDeveloperOrAbove(OrgRolePermission):
    """Bulk incident workflow operations."""

    minimum_role = MembershipRole.DEVELOPER

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        return True
