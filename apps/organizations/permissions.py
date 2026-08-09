"""Role-based access control (Phase 4A).

The role ladder: viewer < developer < admin < owner.

One shared base class (``OrgRolePermission``) resolves the caller's
``Membership`` role for the target organization and compares it against the
class's ``minimum_role``. Subclasses override ``get_org_id`` to resolve the
target org from their own domain object (a project or an incident).

Semantics contract (keeps the existing no-existence-leak behavior):

- Member with insufficient role  → 403 (``PERMISSION_DENIED``)
- Not a member / unknown org     → the permission passes and the view's own
  tenant getter 404s, so foreign ids never surface as a permission error
  (same contract the ``get_*_for_user`` selectors already enforce).
"""

from uuid import UUID

from rest_framework.permissions import BasePermission

from .models import Membership, MembershipRole

ROLE_RANK = {
    MembershipRole.VIEWER: 10,
    MembershipRole.DEVELOPER: 20,
    MembershipRole.ADMIN: 30,
    MembershipRole.OWNER: 40,
}


def role_at_least(role: str | None, minimum: str) -> bool:
    """True when ``role`` ranks at or above ``minimum`` on the ladder."""
    if role is None or minimum is None:
        return False
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum, 0)


def get_membership_role(user, organization_id) -> str | None:
    """The caller's role in an organization, or ``None`` when not a member."""
    return (
        Membership.objects.filter(user=user, organization_id=organization_id)
        .values_list("role", flat=True)
        .first()
    )


def _as_uuid(value) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


class OrgRolePermission(BasePermission):
    """Deny when the caller's org role is below ``minimum_role``.

    The target org id resolves, in order: the view's
    ``get_permission_org_id(request)`` (when the org is picked in the request
    body, e.g. project creation), else ``get_org_id_from_view`` — which
    subclasses override to resolve their own domain object (a project or an
    incident). Returns True for non-members so the view's tenant getter
    decides 404 vs 403.
    """

    minimum_role = MembershipRole.VIEWER
    message = "You do not have permission to do this."

    def get_org_id_from_view(self, request, view) -> UUID | None:
        """Default: the organization id is the ``pk`` URL kwarg."""
        return _as_uuid(view.kwargs.get("pk"))

    def get_org_id(self, request, view) -> UUID | None:
        getter = getattr(view, "get_permission_org_id", None)
        if getter is not None:
            return _as_uuid(getter(request))
        return self.get_org_id_from_view(request, view)

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        org_id = self.get_org_id(request, view)
        if org_id is None:
            return False
        role = get_membership_role(user, org_id)
        if role is None:
            return True  # view's tenant getter 404s for non-members
        return role_at_least(role, self.minimum_role)


class IsOrgMember(OrgRolePermission):
    """Any member of the organization (read-only surfaces)."""

    minimum_role = MembershipRole.VIEWER


class IsOrgOwnerOrAdmin(OrgRolePermission):
    """Org management: invites, project CRUD, API-key rotation."""

    minimum_role = MembershipRole.ADMIN
