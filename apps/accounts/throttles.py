"""Per-endpoint rate limits for the auth API.

Rate strings are configured via ``AUTH_THROTTLE_<SCOPE>`` settings (env-tunable)
instead of ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`` so environments can tune
them without touching code, and tests can override per test.
"""

from rest_framework.throttling import ScopedRateThrottle


class AuthScopedRateThrottle(ScopedRateThrottle):
    """Throttle a view by client IP using its ``throttle_scope``.

    Reads the rate from ``settings.AUTH_THROTTLE_<SCOPE_UPPER>``. A rate of
    ``None`` or empty string disables the throttle.
    """

    def get_rate(self):
        from django.conf import settings

        scope = getattr(self, "scope", None)
        if not scope:
            return None
        suffix = scope.upper().removeprefix("AUTH_")
        return getattr(settings, f"AUTH_THROTTLE_{suffix}", None)
