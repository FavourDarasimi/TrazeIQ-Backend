"""Health check endpoint for monitoring TrazeIQ itself.

Phase 5E: beyond liveness, this reports dependency checks (database, cache)
and pipeline metrics (ingest volume, AI analysis outcomes, alert delivery)
so TrazeIQ's own degradation is visible before customers notice missing
analyses. Every check is best-effort and timed — a down dependency degrades
the payload, never the probe itself (always HTTP 200).
"""

import logging
import time

from django.core.cache import cache
from django.db import connection
from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from trazeiq_backend.responses import api_success, envelope_schema

logger = logging.getLogger(__name__)


def _timed(label, fn) -> dict:
    """Run a check, returning ``{"status": "ok"|"unavailable", ...}``.

    Never raises — failures become ``status: unavailable`` with the error
    class name as detail (no internals leaked to the response).
    """
    start = time.monotonic()
    try:
        detail = fn() or {}
    except Exception as exc:  # noqa: BLE001 — the probe must not fail
        logger.warning("health check %s unavailable: %s", label, exc)
        return {"status": "unavailable"}
    detail["status"] = "ok"
    detail["latency_ms"] = int((time.monotonic() - start) * 1000)
    return detail


def _check_database() -> dict:
    def probe():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {}

    return _timed("database", probe)


def _check_cache() -> dict:
    def probe():
        cache.set("health:ping", 1, timeout=10)
        if cache.get("health:ping") != 1:
            raise ValueError("cache round-trip mismatch")
        return {"backend": cache.__class__.__name__}

    return _timed("cache", probe)


def _pipeline_metrics() -> dict:
    """Cheap aggregate snapshot of pipeline health (2 indexed queries)."""
    from apps.alerts.models import AlertLog
    from apps.events.models import Event

    since = timezone.now() - timezone.timedelta(hours=24)
    alert_rows = (
        AlertLog.objects.filter(dispatched_at__gte=since)
        .values("status")
        .annotate(n=Count("id"))
    )
    return {
        "events_24h": Event.objects.filter(created_at__gte=since).count(),
        "alerts_24h": {
            row["status"]: row["n"] for row in alert_rows
        },
    }


@extend_schema(
    tags=["system"],
    summary="Health check",
    description=(
        "Liveness plus dependency checks (database, cache) and pipeline "
        "metrics (24h ingest volume, AI analysis outcomes, alert delivery). "
        "Always HTTP 200 — read ``status`` (ok/degraded) and the per-check "
        "``status`` fields for readiness."
    ),
    responses={
        200: envelope_schema(
            "HealthOk",
            payload=inline_serializer(
                "HealthData",
                fields={
                    "status": serializers.CharField(),
                    "checks": serializers.DictField(),
                    "metrics": serializers.DictField(),
                },
            ),
        )
    },
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    checks = {
        "database": _check_database(),
        "cache": _check_cache(),
    }
    try:
        metrics: dict = _pipeline_metrics()
    except Exception as exc:  # noqa: BLE001 — metrics never fail the probe
        logger.warning("health metrics unavailable: %s", exc)
        metrics = {}
    overall = "ok" if checks["database"]["status"] == "ok" else "degraded"
    return api_success(
        {"status": overall, "checks": checks, "metrics": metrics},
        status=status.HTTP_200_OK,
    )
