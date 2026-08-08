"""Pusher event publishing helpers (Phase 3A).

The event names/payloads here are the contract the frontend (Phase 3B)
subscribes to on ``private-project-{id}`` channels:

- ``incident.created`` / ``incident.updated``  — fired by the ingestion path
- ``ai_analysis.ready``                        — fired when an analysis lands
- ``incident.resolved``                        — fired by the resolve action
"""

import logging
from uuid import UUID

from apps.events.models import Event
from apps.incidents.models import Incident
from apps.incidents.selectors import latest_events_by_id
from apps.incidents.serializers import IncidentOutputSerializer

from . import pusher

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "private-project-"


def project_channel(project_id: UUID) -> str:
    return f"{CHANNEL_PREFIX}{project_id}"


def _incident_payload(incident: Incident, event: Event | None = None) -> dict:
    """Serialized incident for a push payload.

    ``latest_event`` is resolved from the annotated ``latest_event_id`` plus a
    context map — mirroring what the list/detail views do. When the caller has
    a fresh event in hand (ingestion), we annotate the instance directly so
    the payload always carries it.
    """
    if event is not None:
        incident.latest_event_id = event.id
        events_map = {event.id: event}
    else:
        if hasattr(incident, "latest_event_id"):
            events_map = latest_events_by_id([incident])
        else:
            latest = incident.error_group.events.order_by("-created_at").first()
            incident.latest_event_id = latest.id if latest else None
            events_map = {latest.id: latest} if latest else {}
    return IncidentOutputSerializer(incident, context={"events": events_map}).data


def publish_incident_event(
    incident: Incident, *, event_name: str, event: Event | None = None
) -> bool:
    """Publish an incident lifecycle event (created/updated/resolved) to the
    project channel. Best-effort: never raises."""
    return pusher.publish(
        project_channel(incident.project_id),
        event_name,
        {"incident": _incident_payload(incident, event=event)},
    )


def publish_analysis_ready(incident_id: UUID) -> bool:
    """Publish ``ai_analysis.ready`` once the Celery task finishes an analysis.

    Loads the incident itself so the payload is self-contained — the client
    can drop the pushed analysis straight into its cache without a refetch."""
    try:
        incident = Incident.objects.select_related("error_group").get(pk=incident_id)
    except Incident.DoesNotExist:
        logger.warning("publish_analysis_ready: incident %s no longer exists", incident_id)
        return False
    from apps.ai.models import AIAnalysis

    analysis = (
        AIAnalysis.objects.filter(incident=incident)
        .order_by("-created_at", "-id")
        .first()
    )
    from apps.ai.serializers import AIAnalysisOutputSerializer

    return pusher.publish(
        project_channel(incident.project_id),
        "ai_analysis.ready",
        {
            "incident": _incident_payload(incident),
            "analysis": (
                AIAnalysisOutputSerializer(analysis).data if analysis else None
            ),
        },
    )
