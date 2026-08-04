"""Rate limits for the ingestion endpoint.

Two throttles protect ``POST /api/events/``:
- a global per-IP cap (``EVENT_THROTTLE_IP``), and
- a per-key cap (``EVENT_THROTTLE_KEY``) so one misbehaving integration key
  cannot starve the endpoint for everyone else.

Both are env-tunable via settings, mirroring the auth throttles.
"""

from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class EventScopedRateThrottle(ScopedRateThrottle):
    """Rate string from ``settings.EVENT_THROTTLE_<SCOPE_UPPER>``.

    Overrides ``get_cache_key`` because the DRF base reads
    ``request.user.is_authenticated`` — a property that only exists on User,
    not on the Project that authenticates ingestion requests.
    """

    def get_rate(self):
        from django.conf import settings

        scope = getattr(self, "scope", None)
        if not scope:
            return None
        suffix = scope.upper().removeprefix("EVENT_")
        return getattr(settings, f"EVENT_THROTTLE_{suffix}", None)

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": getattr(self, "scope", ""),
            "ident": self.get_ident(request),
        }


class EventPerKeyRateThrottle(SimpleRateThrottle):
    """Throttle scoped per authenticated Project (API key holder).

    ``request.user`` is the ``Project`` from ``APIKeyAuthentication`` (never a
    ``User`` here), so the cache key is built from its pk instead of the
    caller's IP. Prevents one integration key from hammering ingestion.
    """

    scope = "event_per_key"

    def get_rate(self):
        from django.conf import settings

        return getattr(settings, "EVENT_THROTTLE_KEY", None)

    def get_cache_key(self, request, view):
        if request.user and getattr(request.user, "pk", None):
            return self.cache_format % {"scope": self.scope, "ident": request.user.pk}
        return self.cache_format % {"scope": self.scope, "ident": request.META.get("REMOTE_ADDR", "")}