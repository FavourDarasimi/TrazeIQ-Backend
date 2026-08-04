from rest_framework.permissions import BasePermission

from apps.projects.models import Project


class IsAPIKeyAuthenticated(BasePermission):
    """The caller authenticated as a ``Project`` via ``X-API-Key``.

    The ingestion endpoint is a system-agent surface (not a human), so it is
    gated by this permission rather than the default JWT permission.
    """

    def has_permission(self, request, view):
        return isinstance(request.user, Project)