from django.db.models import F
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from apps.projects.models import ApiKeyScope, Project, ProjectApiKey
from apps.projects.utils import hash_api_key


class APIKeyAuthentication(BaseAuthentication):
    """`X-API-Key` authentication for the ingestion endpoints.

    The incoming key is hashed (deterministic HMAC-SHA256) and verified against
    ``ProjectApiKey`` (checking validity, grace period, and required scopes).
    ``last_used_at`` and ``request_count`` are updated atomically.

    Falls back to legacy ``Project.api_key_hash`` for backwards compatibility.
    ``request.user`` is the authenticated ``Project``; ``request.auth`` is the
    ``ProjectApiKey`` (or None).
    """

    header = "X-API-Key"

    def authenticate_header(self, request):
        # Without this DRF coerces auth failures to 403 (it only keeps the
        # 401 when a WWW-Authenticate header is advertised).
        return self.header

    def authenticate(self, request):
        raw_key = request.headers.get(self.header)
        if not raw_key:
            raise NotAuthenticated("API key required.")

        digest = hash_api_key(raw_key)

        api_key = (
            ProjectApiKey.objects.filter(api_key_hash=digest)
            .select_related("project", "project__organization")
            .first()
        )
        if api_key is not None:
            if not api_key.is_valid:
                raise NotAuthenticated("API key revoked or expired.")
            if not api_key.has_scope(ApiKeyScope.INGEST_ONLY):
                raise PermissionDenied("API key lacks 'ingest_only' scope.")

            # Record usage auditing: update last_used_at and bump request_count
            ProjectApiKey.objects.filter(pk=api_key.pk).update(
                last_used_at=timezone.now(),
                request_count=F("request_count") + 1,
            )
            return (api_key.project, api_key)

        project = (
            Project.objects.filter(api_key_hash=digest)
            .select_related("organization")
            .first()
        )
        if project is None:
            raise NotAuthenticated("Invalid API key.")
        return (project, None)
