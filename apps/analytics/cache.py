"""Dashboard aggregate caching (Phase 5C).

Caching strategy: each request's aggregates are cached keyed by the set of
projects the *caller can see* plus a per-project version counter. A write to
an ``Event`` or ``Incident`` bumps that project's version (see
``apps/analytics/signals.py``), which changes the cache key and orphans the
stale entry — invalidation without fan-out, so a new event is reflected within
a request or two even under heavy multi-user load. A short TTL is a safety net
for the rare write that emits no signal (bulk operations, manual deletes).

Every cached payload carries the cold-compute time so we can log the time
saved on a cache hit (DoD: measure query time saved vs the uncached path).
"""

import logging
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_VERSION_PREFIX = "dashboard:version:project:"
_DEFAULT_TTL_SECONDS = 60


def _ttl() -> int:
    return getattr(settings, "DASHBOARD_CACHE_TTL_SECONDS", _DEFAULT_TTL_SECONDS)


def bump_project_dashboard_version(project_id) -> None:
    """Mark a project's dashboard aggregates stale.

    A missing counter is created at 1; races only cost a little version skew
    and never suppress invalidation, so this is safe to call from a signal.
    Versions never expire (``timeout=None``) — if they did, a key could revert
    to ``v0`` and reuse a stale entry still inside its TTL.
    """
    key = f"{_VERSION_PREFIX}{project_id}"
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=None)


def _project_versions(project_ids) -> list[int]:
    return [cache.get(f"{_VERSION_PREFIX}{pid}") or 0 for pid in project_ids]


def build_dashboard_key(kind: str, project_ids, range_: str | None = None) -> str:
    """The cache key for one aggregate: kind + sorted project ids + their
    current versions (+ range for time-series stats). Bumping any project's
    version changes this key, so stale entries are orphaned, not served."""
    ids = sorted(str(pid) for pid in project_ids)
    versions = _project_versions(ids)
    key = f"dashboard:{kind}:{':'.join(ids)}:v{':'.join(map(str, versions))}"
    if range_:
        key += f":{range_}"
    return key


def cached_dashboard(kind: str, project_ids, range_: str | None, compute) -> dict:
    """Return the cached aggregate, populating it on a miss.

    ``compute`` is a zero-arg callable performing the (expensive) query. On a
    hit we log the cold time we avoided; on a miss we time the compute and log
    it as the baseline.
    """
    key = build_dashboard_key(kind, project_ids, range_)
    hit = cache.get(key)
    if hit is not None:
        logger.info(
            "dashboard cache HIT key=%s saved~%sms", key, hit.get("_compute_ms")
        )
        return hit["data"]

    start = time.monotonic()
    data = compute()
    compute_ms = int((time.monotonic() - start) * 1000)
    logger.info("dashboard cache MISS key=%s cold_compute=%sms", key, compute_ms)
    cache.set(
        key, {"data": data, "_compute_ms": compute_ms}, timeout=_ttl()
    )
    return data
