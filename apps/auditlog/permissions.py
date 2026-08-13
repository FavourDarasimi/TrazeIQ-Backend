from rest_framework.permissions import BasePermission

from apps.organizations.models import Membership, MembershipRole


class IsOrgOwnerOrAdminAny(BasePermission):
    """True when the caller is an owner/admin of at least one organization.

    Used by the audit-log list endpoint: a developer (no admin org) receives
    403, while any owner/admin may list the audit logs of the orgs they
    administer.
    """

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        return Membership.objects.filter(
            user=user,
            role__in=[MembershipRole.OWNER, MembershipRole.ADMIN],
        ).exists()
