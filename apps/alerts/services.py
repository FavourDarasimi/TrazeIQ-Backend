"""Phase 4C: alert rule matching, cooldown enforcement and dispatch logging.

The evaluation chain, all best-effort for the caller:

    enqueue_alert_evaluation (this module, synchronous)
      -> evaluate_incident (this module)
           -> one AlertLog row per matched rule out of cooldown

``enqueue_alert_evaluation`` is the hook callers (ingestion, incident PATCH)
use — evaluation is cheap DB work plus best-effort dispatches, so it runs
inline; any failure degrades to "no alerts" instead of failing the request.
"""

import logging
from datetime import timedelta
from uuid import UUID

from django.utils import timezone

from apps.incidents.models import Incident

from .models import AlertLog, AlertRule

logger = logging.getLogger(__name__)


def rule_matches(rule: AlertRule, incident: Incident) -> bool:
    """True when every condition key agrees with the incident's current
    state. Conditions were validated at creation, so keys are known and
    values are valid choices — nothing here can throw."""
    condition = rule.condition or {}
    if "severity" in condition and incident.severity != condition["severity"]:
        return False
    if "status" in condition and incident.status != condition["status"]:
        return False
    return True


def evaluate_incident(incident: Incident) -> int:
    """Score the incident against the project's rules and dispatch one alert
    per matched rule that is outside its cooldown window.

    Returns the number of dispatch attempts logged. Suppressed (in-cooldown)
    matches are skipped without logging; a failed delivery is still logged
    with ``status=failed`` and the error detail, so the AlertLog table stays
    a faithful record of attempts. Dispatch never raises — one bad webhook
    must not take down the request.
    """
    from .dispatchers import dispatch

    now = timezone.now()
    dispatches = 0
    for rule in AlertRule.objects.filter(project=incident.project_id):
        if not rule_matches(rule, incident):
            continue
        cutoff = now - timedelta(minutes=rule.cooldown_minutes)
        if AlertLog.objects.filter(
            rule=rule, incident=incident, dispatched_at__gte=cutoff
        ).exists():
            continue
        log = AlertLog.objects.create(rule=rule, incident=incident)
        dispatches += 1
        try:
            dispatch(rule, incident)
        except Exception as exc:  # noqa: BLE001 — one bad channel must not
            # take down the other rules or the worker.
            log.status = AlertLog.Status.FAILED
            log.error = str(exc)[:500]
            log.save(update_fields=["status", "error"])
            logger.warning(
                "dispatch failed for rule %s incident %s: %s",
                rule.id,
                incident.id,
                exc,
            )
    return dispatches


def enqueue_alert_evaluation(incident_id: UUID) -> None:
    """Evaluate the incident's alert rules synchronously.

    No worker queue: matching is cheap DB work and each dispatch is already
    best-effort, so this runs inline in the request path. Never raises — any
    failure is swallowed+logged so ingestion (and incident PATCH) never 500
    because of alerts.
    """
    try:
        incident = Incident.objects.select_related("project").get(pk=incident_id)
    except Incident.DoesNotExist:
        logger.warning(
            "enqueue_alert_evaluation: incident %s no longer exists",
            incident_id,
        )
        return
    try:
        dispatches = evaluate_incident(incident)
    except Exception:
        logger.exception(
            "enqueue_alert_evaluation: evaluation failed for %s",
            incident_id,
        )
        return
    if dispatches:
        logger.info(
            "enqueue_alert_evaluation: %s dispatch(es) for incident %s",
            dispatches,
            incident_id,
        )