"""Platform (site-wide) admin access control.

TrazeIQ's product roles (owner/admin/developer/viewer) are org-scoped — they
govern tenant data. Platform monitoring is a different principal: the
operator running TrazeIQ itself. That gate is Django's built-in ``is_staff``
flag, enforced here in a DRF permission class (never hidden in the UI).

- Unauthenticated → 401 (via the default auth class, same as every endpoint)
- Authenticated but not staff → 403 ``PERMISSION_DENIED``
- Staff → full read access across all tenants (this surface is
  deliberately *not* tenant-scoped — monitoring the website means seeing
  every organization, project and pipeline metric)
"""

from rest_framework.permissions import BasePermission


class IsStaff(BasePermission):
    """Allow only authenticated users with ``is_staff=True``."""

    message = "Platform administration is restricted to staff users."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user is not None
            and user.is_authenticated
            and user.is_staff
        )
