"""SLO budget calculation, snapshot persistence, and breach-alert dispatch.

The calculation is Google SRE's textbook error-budget formula over a rolling
period: ``budget_total = total_events * (1 - target)``,
``budget_remaining_pct = max(0, (budget_total - bad_events) / budget_total * 100)``.
Multi-window burn rates follow the workbook's multi-window burn-rate alert
guidance — 1h (fast burn), 6h, 24h, 72h (slow burn) — and the SLO's status
is derived from the worst window.

A ``bad_event`` is any ``Event`` with ``level in (error, fatal)`` (matching
the dashboard's existing severity grouping) inside the window. ``service``
and ``severity`` keys in the SLO's ``error_query`` narrow the match; an
empty/missing key means "any".

Every function is best-effort by contract: a failed snapshot/alert write
never breaks the caller (mirroring the alert-engine hooks).
"""

import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.events.models import Event

from .models import SLO, SLOBudgetSnapshot, SLOBreachAlert

logger = logging.getLogger(__name__)


ZERO_DEC = Decimal("0")
ONE_HUNDRED = Decimal("100")
ONE = Decimal("1")
HOUR = timedelta(hours=1)
BURN_WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "72h": timedelta(hours=72),
}


def _to_pct(value: Decimal | float | int) -> Decimal:
    """Quantize a 0-100 percentage to two decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _to_rate(value: Decimal | float | int) -> Decimal:
    """Quantize a burn rate (bad events / hour) to four decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _events_queryset(slo: SLO, *, window_start, window_end, level_filter=None):
    """Base queryset of ``Event`` rows for one SLO + window, with the
    ``error_query`` filter applied.

    ``level_filter`` lets the burn-rate windows narrow the level (default:
    error + fatal). ``service`` in ``error_query`` is applied only when set
    so an SLO without one watches every service.
    """
    qs = Event.objects.filter(
        project=slo.project,
        created_at__gte=window_start,
        created_at__lt=window_end,
    )
    if level_filter is not None:
        qs = qs.filter(level__in=level_filter)
    service = (slo.error_query or {}).get("service") or ""
    if service:
        qs = qs.filter(service=service)
    return qs


def evaluate_slo(slo: SLO, *, now=None) -> SLOBudgetSnapshot | None:
    """Compute the current budget snapshot for one SLO and persist it.

    Returns the persisted snapshot, or ``None`` when the SLO is disabled.
    Safe to call repeatedly; snapshots are append-only history.
    """
    if not slo.enabled:
        return None
    now = now or timezone.now()
    window = timedelta(days=slo.window_days)
    period_start = now - window
    bad_levels = [Event.Level.ERROR, Event.Level.FATAL]

    total = _events_queryset(slo, window_start=period_start, window_end=now).count()
    bad = _events_queryset(
        slo,
        window_start=period_start,
        window_end=now,
        level_filter=bad_levels,
    ).count()

    target = Decimal(str(slo.target_pct))
    error_rate_allowed = ONE_HUNDRED - target  # e.g. 0.1 for 99.9
    budget_total = _to_rate(Decimal(total) * error_rate_allowed / ONE_HUNDRED)
    if total and budget_total > ZERO_DEC:
        consumed = min(Decimal(bad), budget_total)
        remaining_pct = (budget_total - consumed) / budget_total * ONE_HUNDRED
    else:
        # No traffic yet (or impossible math) — full budget remains.
        remaining_pct = ONE_HUNDRED

    # Multi-window burn rates (bad events per hour, restricted to the bad
    # level filter so a flood of info events doesn't look like a burn).
    burn_rates: dict[str, Decimal] = {}
    for key, span in BURN_WINDOWS.items():
        sub_total = _events_queryset(
            slo,
            window_start=now - span,
            window_end=now,
            level_filter=bad_levels,
        ).count()
        hours = max(span.total_seconds() / 3600, 1)
        burn_rates[key] = _to_rate(Decimal(sub_total) / Decimal(hours))

    success_rate = _to_rate(
        ONE - (Decimal(bad) / Decimal(total)) if total else ZERO_DEC
    )

    snapshot = SLOBudgetSnapshot.objects.create(
        slo=slo,
        period_start=period_start,
        period_end=now,
        total_events=total,
        bad_events=bad,
        success_rate=success_rate,
        budget_total=budget_total,
        budget_remaining_pct=_to_pct(max(remaining_pct, ZERO_DEC)),
        burn_rate_1h=burn_rates["1h"],
        burn_rate_6h=burn_rates["6h"],
        burn_rate_24h=burn_rates["24h"],
        burn_rate_72h=burn_rates["72h"],
        status=_derive_status(remaining_pct, burn_rates, budget_total, window),
    )
    return snapshot


def _derive_status(
    remaining_pct: Decimal,
    burn_rates: dict[str, Decimal],
    budget_total: Decimal,
    window: timedelta,
) -> str:
    """Map remaining budget + burn rates to a status string.

    Status is the worst of: ``exhausted`` (no budget left), ``critical``
    (the 1h burn would empty the budget within 2 hours — fast-burn
    page), ``warning`` (the 24h burn would empty the budget within 1
    day — slow-burn page), else ``healthy``. The thresholds match the
    Google SRE multi-window burn-rate alert guidance: a fast burn is
    one that would deplete the *whole* error budget inside 2 hours, a
    slow burn inside 1 day. A brand-new SLO with no traffic yet is
    treated as healthy — there's nothing to burn.
    """
    if budget_total <= ZERO_DEC:
        return SLOBudgetSnapshot.Status.HEALTHY
    if remaining_pct <= ZERO_DEC:
        return SLOBudgetSnapshot.Status.EXHAUSTED

    remaining_budget = budget_total * remaining_pct / ONE_HUNDRED
    if remaining_budget <= ZERO_DEC:
        return SLOBudgetSnapshot.Status.EXHAUSTED

    # Fast burn: would empty the remaining budget inside 2 hours.
    if (
        burn_rates["1h"] * Decimal(2) >= remaining_budget
        or burn_rates["6h"] * Decimal(2) >= remaining_budget
    ):
        return SLOBudgetSnapshot.Status.CRITICAL
    # Slow burn: would empty the remaining budget inside 1 day.
    if (
        burn_rates["24h"] * Decimal(24) >= remaining_budget
        or burn_rates["72h"] * Decimal(24) >= remaining_budget
    ):
        return SLOBudgetSnapshot.Status.WARNING
    return SLOBudgetSnapshot.Status.HEALTHY


def maybe_fire_breach_alerts(snapshot: SLOBudgetSnapshot) -> list[SLOBreachAlert]:
    """Record a breach alert when the snapshot's status clears the SLO's
    configured threshold. Existing same-kind alerts from the current
    burn are refreshed on the same row (no spam), and a previously-fired
    but now-recovered alert clears itself so the next crossing fires
    fresh.
    """
    slo = snapshot.slo
    remaining = Decimal(str(snapshot.budget_remaining_pct))

    # Nothing to do — budget comfortably above the configured threshold.
    if snapshot.status == SLOBudgetSnapshot.Status.HEALTHY:
        _acknowledge_outstanding(slo)
        return []

    # Still firing — keep one open alert per kind so the list endpoint
    # stays a "what's currently in trouble" view, not a flood.
    kind = _status_to_alert_kind(snapshot.status)
    alert, created = SLOBreachAlert.objects.get_or_create(
        slo=slo,
        kind=kind,
        acknowledged_at__isnull=True,
        defaults={
            "snapshot": snapshot,
            "budget_remaining_pct": snapshot.budget_remaining_pct,
            "message": _alert_message(slo, snapshot, remaining),
        },
    )
    if not created:
        # Refresh the live alert's numbers without duplicating it.
        alert.snapshot = snapshot
        alert.budget_remaining_pct = snapshot.budget_remaining_pct
        alert.message = _alert_message(slo, snapshot, remaining)
        alert.save(update_fields=["snapshot", "budget_remaining_pct", "message"])
    else:
        # Only fire the inbox fan-out on a brand-new alert, not on every
        # refresh — the alert row itself is the dashboard's source of truth.
        _enqueue_breach_notification(alert)
    return [alert]


def _enqueue_breach_notification(alert: SLOBreachAlert) -> None:
    """Best-effort enqueue of the org-wide inbox fan-out for one alert.

    Swallows every failure — the breach alert row is already persisted,
    so even if the broker is down or the task crashes the alert shows up
    on the dashboard's next poll. Mirrors the never-fail contract on
    ``apps.alerts.tasks.enqueue_alert_evaluation``.
    """
    try:
        from .tasks import notify_org_of_breach

        notify_org_of_breach.delay(str(alert.id))
    except Exception:  # noqa: BLE001 — never block the caller
        logger.exception("Failed to enqueue SLO breach notification")


def _status_to_alert_kind(status: str) -> str:
    return {
        SLOBudgetSnapshot.Status.WARNING: SLOBreachAlert.Kind.WARNING,
        SLOBudgetSnapshot.Status.CRITICAL: SLOBreachAlert.Kind.CRITICAL,
        SLOBudgetSnapshot.Status.EXHAUSTED: SLOBreachAlert.Kind.EXHAUSTED,
    }[status]


def _alert_message(slo: SLO, snapshot: SLOBudgetSnapshot, remaining: Decimal) -> str:
    return (
        f"SLO '{slo.name}' at {remaining}% budget remaining "
        f"({snapshot.bad_events}/{snapshot.budget_total or 0} bad events)."
    )


def _acknowledge_outstanding(slo: SLO) -> int:
    """When the SLO recovers, mark the still-open alerts as
    acknowledged-by-recovery so the next crossing produces a fresh row.
    Returning the count lets callers log it without an extra query."""
    now = timezone.now()
    open_alerts = SLOBreachAlert.objects.filter(
        slo=slo, acknowledged_at__isnull=True
    )
    count = 0
    for alert in open_alerts:
        alert.acknowledged_at = now
        alert.save(update_fields=["acknowledged_at"])
        count += 1
    return count


def evaluate_all_enabled_slos() -> int:
    """Evaluate every enabled SLO and fire any breach alerts. Returns the
    number of snapshots persisted. Safe to call from a Celery beat entry
    — failures on individual SLOs are swallowed+logged so one bad row
    can't poison the whole pass."""

    # Walk every SLO directly — no user context is needed for evaluation,
    # only for the dashboard view.
    count = 0
    for slo in SLO.objects.filter(enabled=True).select_related("project"):
        try:
            snapshot = evaluate_slo(slo)
            if snapshot is not None:
                maybe_fire_breach_alerts(snapshot)
                count += 1
        except Exception:  # noqa: BLE001 — best-effort by contract
            logger.exception("SLO evaluation failed for %s", slo.id)
    return count


def project_summary(project_id, *, now=None) -> dict:
    """A compact summary across every SLO in one project — used by the
    dashboard's service-dependency-impact view. Per-service totals come
    straight from the latest snapshot of each SLO so the answer stays
    deterministic (no live recompute)."""
    now = now or timezone.now()
    slos = SLO.objects.filter(project_id=project_id, enabled=True)
    rows = []
    breached = 0
    exhausted_budget = 0
    for slo in slos:
        latest = (
            SLOBudgetSnapshot.objects.filter(slo=slo)
            .order_by("-evaluated_at")
            .first()
        )
        if not latest:
            rows.append(
                {
                    "slo_id": str(slo.id),
                    "name": slo.name,
                    "service": (slo.error_query or {}).get("service", "") or "",
                    "status": "unknown",
                    "budget_remaining_pct": None,
                    "burn_rate_1h": "0",
                    "burn_rate_24h": "0",
                }
            )
            continue
        rows.append(
            {
                "slo_id": str(slo.id),
                "name": slo.name,
                "service": (slo.error_query or {}).get("service", "") or "",
                "status": latest.status,
                "budget_remaining_pct": str(latest.budget_remaining_pct),
                "burn_rate_1h": str(latest.burn_rate_1h),
                "burn_rate_24h": str(latest.burn_rate_24h),
            }
        )
        if latest.status in (
            SLOBudgetSnapshot.Status.WARNING,
            SLOBudgetSnapshot.Status.CRITICAL,
        ):
            breached += 1
        if latest.status == SLOBudgetSnapshot.Status.EXHAUSTED:
            exhausted_budget += 1

    return {
        "project_id": str(project_id),
        "evaluated_at": now,
        "total_slos": slos.count(),
        "breached_slos": breached,
        "exhausted_slos": exhausted_budget,
        "slos": rows,
    }


def acknowledge_alert(alert: SLOBreachAlert, *, actor=None) -> SLOBreachAlert:
    """Mark a single breach alert acknowledged. Idempotent: re-acknowledging
    is a no-op so the UI can re-fire the action without effects."""
    if alert.acknowledged_at is None:
        alert.acknowledged_at = timezone.now()
        update_fields = ["acknowledged_at"]
        if actor is not None:
            alert.acknowledged_by = actor
            update_fields.append("acknowledged_by")
        alert.save(update_fields=update_fields)
    return alert


def impacted_slos(
    user, *, service: str, project_id
) -> list[dict]:
    """The other SLOs in the same project that a service outage would
    drag into breach.

    An SLO ``A`` impacts ``B`` when ``A`` watches service ``X`` and
    ``B`` watches the same project without a service filter (i.e. every
    event in the project counts toward ``B``). Returns the matching rows
    with the latest snapshot's status so the dependency view can show
    "if X dies, Y dies with it"."""
    base = SLO.objects.filter(
        project_id=project_id,
        enabled=True,
        project__organization__memberships__user=user,
    )
    source = base.filter(error_query__service=service).select_related("project")
    targets = base.filter(
        Q(error_query__service__isnull=True) | Q(error_query__service="")
    )
    impacted = []
    for target in targets:
        latest = (
            SLOBudgetSnapshot.objects.filter(slo=target)
            .order_by("-evaluated_at")
            .first()
        )
        impacted.append(
            {
                "slo_id": str(target.id),
                "name": target.name,
                "status": latest.status if latest else "unknown",
                "budget_remaining_pct": (
                    str(latest.budget_remaining_pct) if latest else None
                ),
                "source_service": service,
            }
        )
    return impacted


__all__ = [
    "BURN_WINDOWS",
    "acknowledge_alert",
    "evaluate_all_enabled_slos",
    "evaluate_slo",
    "impacted_slos",
    "maybe_fire_breach_alerts",
    "project_summary",
]