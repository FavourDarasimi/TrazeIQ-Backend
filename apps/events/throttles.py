"""Redis-backed rate limits for the ingestion endpoint.

Two throttles protect ``POST /api/events/``:
- a global per-client-IP cap (``EVENT_THROTTLE_IP``), and
- a per-project (API-key) cap that defaults to ``EVENT_THROTTLE_KEY`` but is
  overridden per project by ``Project.events_per_minute``.

Both are env-tunable and backed by the configured cache (Redis in
production). Counting uses an atomic ``cache.incr`` so the cap holds under
concurrent/parallel requests, not just sequential ones — a fixed window that
resets via the cache key's TTL. DRF's stock ``SimpleRateThrottle`` stores a
history list and is not atomic under concurrency, so we count instead.
"""

from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

_PERIOD_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_rate(rate):
    """Parse a DRF rate string like ``"1000/min"`` into ``(count, seconds)``."""
    if not rate or not isinstance(rate, str):
        return None
    try:
        count_str, period = rate.strip().split("/")
        return (int(count_str), _PERIOD_SECONDS[period[0]])
    except (ValueError, KeyError, IndexError):
        return None


def _client_ip(request):
    ident = request.META.get("REMOTE_ADDR", "")
    if not ident:
        ident = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    return ident or "unknown"


class RedisWindowThrottle(BaseThrottle):
    """Fixed-window request counter backed by the configured cache.

    Subclasses implement :meth:`get_cache_key` and :meth:`get_rate`.
    """

    def get_cache_key(self, request, view):
        raise NotImplementedError

    def get_rate(self, request, view):
        return None

    def allow_request(self, request, view):
        rate = self.get_rate(request, view)
        parsed = _parse_rate(rate)
        if parsed is None:
            # No limit configured — let it through.
            return True
        num, duration = parsed
        key = self.get_cache_key(request, view)
        try:
            count = cache.incr(key, 1)
        except ValueError:
            # Backend doesn't auto-create on incr — seed it.
            cache.add(key, 1, timeout=duration)
            count = 1
        if count == 1:
            # Seed the window's TTL on the first hit so it resets.
            cache.touch(key, duration)
        if count > num:
            # Window resets after `duration`; tell the client to wait that long.
            self._retry_after = duration
            return False
        return True

    def wait(self):
        return getattr(self, "_retry_after", None)


class EventPerIpRateThrottle(RedisWindowThrottle):
    """Global per-client-IP cap on ingestion.

    Deters a stolen key being sprayed across many project credentials, and
    keeps one noisy source from saturating the endpoint for everyone.
    """

    def get_cache_key(self, request, view):
        return f"throttle:event:ip:{_client_ip(request)}"

    def get_rate(self, request, view):
        from django.conf import settings

        return getattr(settings, "EVENT_THROTTLE_IP", "5000/min")


class EventPerKeyRateThrottle(RedisWindowThrottle):
    """Per-project (API-key) cap — the primary guard for ingestion.

    ``request.user`` is the authenticated ``Project``; if it carries a
    non-zero ``events_per_minute`` we use that, so the cap is configurable per
    project. Otherwise we fall back to the global ``EVENT_THROTTLE_KEY``.
    """

    def get_cache_key(self, request, view):
        ident = getattr(getattr(request, "user", None), "pk", None)
        if not ident:
            ident = _client_ip(request)
        return f"throttle:event:key:{ident}"

    def get_rate(self, request, view):
        user = getattr(request, "user", None)
        per_project = getattr(user, "events_per_minute", None)
        if per_project:
            return f"{per_project}/min"
        from django.conf import settings

        return getattr(settings, "EVENT_THROTTLE_KEY", "1000/min")
