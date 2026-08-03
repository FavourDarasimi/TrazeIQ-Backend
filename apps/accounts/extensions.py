"""OpenAPI extensions for the accounts app (drf-spectacular)."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class AccessCookieJWTAuthScheme(OpenApiAuthenticationExtension):
    """Describe ``AccessCookieJWTAuthentication`` for the schema.

    The class authenticates via ``Authorization: Bearer <token>`` or the
    httpOnly ``trazeiq_access`` cookie, so two alternates are documented.
    """

    target_class = "apps.accounts.authentication.AccessCookieJWTAuthentication"
    name = ["bearerAuth", "accessCookieAuth"]
    match_subclasses = True

    def get_security_definition(self, auto_schema):
        return [
            {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            {"type": "apiKey", "in": "cookie", "name": "trazeiq_access"},
        ]

    def get_security_requirement(self, auto_schema):
        # Either the Bearer header or the cookie satisfies auth (alternative,
        # not both) — hence the list form (OR).
        return [{"bearerAuth": []}, {"accessCookieAuth": []}]