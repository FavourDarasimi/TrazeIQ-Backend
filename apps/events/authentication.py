from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import NotAuthenticated

from apps.projects.models import Project
from apps.projects.utils import hash_api_key


class APIKeyAuthentication(BaseAuthentication):
    """`X-API-Key` authentication for the ingestion endpoints.

    The incoming key is hashed (deterministic HMAC-SHA256, the same digest
    stored on ``Project.api_key_hash``) and the project is looked up by that
    digest — the raw key never touches a query.

    ``request.user`` is the authenticated ``Project``; ``IsAPIKeyAuthenticated``
    is the matching permission class.
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
        project = (
            Project.objects.filter(api_key_hash=hash_api_key(raw_key))
            .select_related("organization")
            .first()
        )
        if project is None:
            raise NotAuthenticated("Invalid API key.")
        return (project, None)