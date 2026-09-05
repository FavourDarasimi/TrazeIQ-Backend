"""Celery tasks for the SLO engine.

``evaluate_all_slos`` runs the budget calculator over every enabled SLO
and fires any breach alerts. It is safe to enqueue from anywhere (a
management command, a periodic beat schedule, or the dashboard's
``evaluate-now`` button) — failures on individual SLOs are swallowed so
one bad row never poisons the whole pass.
"""

import logging

from celery import shared_task

from .services import evaluate_all_enabled_slos, maybe_fire_breach_alerts
from .selectors import list_recent_alerts_for_org
from .models import SLOBudgetSnapshot

logger = logging.getLogger(__name__)


@shared_task(name="apps.slos.tasks.evaluate_all_slos")
def evaluate_all_slos() -> int:
    """Evaluate every enabled SLO once. Returns the count of snapshots
    persisted; logging captures per-row failures so a periodic run can
    diagnose without raising."""
    try:
        count = evaluate_all_enabled_slos()
    except Exception:  # noqa: BLE001
        logger.exception("SLO batch evaluation failed")
        return 0
    logger.info("SLO evaluation pass: %s snapshot(s)", count)
    return count


@shared_task(name="apps.slos.tasks.notify_org_of_breach")
def notify_org_of_breach(alert_id: str) -> None:
    """Best-effort in-app fan-out when a breach alert fires.

    Wraps the existing notification fan-out so the SLO breach never breaks
    the caller — the same never-fail contract as ``apps.alerts.tasks`` and
    ``apps.notifications.services``. The breach alert row itself is the
    system of record the dashboard polls; this hook exists so we have a
    single place to extend when we want inbox delivery.
    """
    from .models import SLOBreachAlert
    from apps.notifications.models import Notification
    from apps.organizations.models import Membership

    try:
        alert = SLOBreachAlert.objects.select_related("slo", "slo__project").get(
            id=alert_id
        )
    except SLOBreachAlert.DoesNotExist:
        logger.warning("notify_org_of_breach: alert %s no longer exists", alert_id)
        return

    try:
        recipient_ids = list(
            Membership.objects.filter(
                organization_id=alert.slo.project.organization_id
            ).values_list("user_id", flat=True)
        )
        rows = [
            Notification(
                recipient_id=rid,
                incident=None,
                kind=Notification.Kind.SYSTEM,
                title=f"SLO '{alert.slo.name}' is in breach",
                body=alert.message,
            )
            for rid in recipient_ids
        ]
        if rows:
            Notification.objects.bulk_create(rows)
    except Exception:  # noqa: BLE001
        logger.exception("SLO breach fan-out failed for alert %s", alert_id)