"""OpenAPI extensions for the events app (drf-spectacular)."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class APIKeyAuthScheme(OpenApiAuthenticationExtension):
    """Describe ``APIKeyAuthentication`` for the schema.

    The ingestion endpoints authenticate with the project's raw API key via
    the ``X-API-Key`` header — a plain apiKey scheme (no bearer format).
    """

    target_class = "apps.events.authentication.APIKeyAuthentication"
    name = "apiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "X-API-Key"}

    def get_security_requirement(self, auto_schema):
        return [{"apiKeyAuth": []}]