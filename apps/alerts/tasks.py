"""Background job for alert evaluation (Phase 4C).

The task only loads the incident and delegates to the service — business
logic stays in ``apps.alerts.services.evaluate_incident`` (architecture
rule: a task calls a service).
"""

import logging
from uuid import UUID

from celery import shared_task

from apps.incidents.models import Incident

from .services import evaluate_incident

logger = logging.getLogger(__name__)


@shared_task(name="apps.alerts.tasks.evaluate_alerts_for_incident")
def evaluate_alerts_for_incident(incident_id: UUID):
    """Match the incident against its project's rules and record dispatches.

    Runs on the default queue from the same points where
    ``incident.created`` / ``incident.updated`` fire, so it can never block
    the ingestion response. The cooldown window inside the service is what
    prevents alert storms.
    """
    try:
        incident = Incident.objects.select_related("project").get(pk=incident_id)
    except Incident.DoesNotExist:
        logger.warning(
            "evaluate_alerts_for_incident: incident %s no longer exists",
            incident_id,
        )
        return
    dispatches = evaluate_incident(incident)
    if dispatches:
        logger.info(
            "evaluate_alerts_for_incident: %s dispatch(es) for incident %s",
            dispatches,
            incident_id,
        )