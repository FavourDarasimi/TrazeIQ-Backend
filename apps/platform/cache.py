"""TTL cache for the platform overview aggregate.

Unlike the tenant dashboard cache (per-project versions bumped by signals),
the platform overview is a single low-traffic internal payload — a short TTL
(``PLATFORM_ADMIN_CACHE_SECONDS``, default 60s) is the whole strategy. Every
list endpoint stays uncached and paginated instead, so operators always see
fresh rows.
"""

from django.conf import settings
from django.core.cache import cache

_OVERVIEW_KEY = "platform:overview"
_DEFAULT_TTL_SECONDS = 60


def _ttl() -> int:
    return getattr(
        settings, "PLATFORM_ADMIN_CACHE_SECONDS", _DEFAULT_TTL_SECONDS
    )


def cached_overview(compute):
    """Return the cached overview, populating it on a miss."""
    hit = cache.get(_OVERVIEW_KEY)
    if hit is not None:
        return hit
    data = compute()
    cache.set(_OVERVIEW_KEY, data, timeout=_ttl())
    return data
