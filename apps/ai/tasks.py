"""Background jobs for the ai app — auto-routed to the rate-limited
``ai_analysis`` queue by ``CELERY_TASK_ROUTES`` in settings/base.py.

The task only decides retry policy and final failure state; the actual LLM
work lives in ``apps.ai.services.run_analysis`` (architecture rule: a task
calls a service, it never contains business logic).
"""

import logging
from uuid import UUID

from celery import shared_task
from django.conf import settings

from apps.incidents.models import Incident
from apps.realtime.services import publish_analysis_ready

from .openrouter import OpenRouterAPIError, RateLimitError
from .services import (
    AnalysisFormatError,
    mark_analysis_failed,
    retry_countdown,
    run_analysis,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="apps.ai.tasks.analyze_incident")
def analyze_incident(self, incident_id: UUID):
    """Analyze one incident's error pattern via OpenRouter (spec §7).

    Retry-able failures (429, network/5xx) back off exponentially through
    Celery's retry mechanism instead of dropping the job; after
    ``AI_RETRY_MAX_ATTEMPTS`` the analysis is marked failed rather than
    retried forever. Non-retryable outcomes (malformed JSON twice, missing
    API key, unexpected exceptions) mark the analysis failed without crashing
    the worker.
    """
    if not settings.OPENROUTER_API_KEY:
        logger.warning(
            "OPENROUTER_API_KEY unset — analysis failed for incident %s",
            incident_id,
        )
        mark_analysis_failed(
            incident_id, reason="OPENROUTER_API_KEY not configured"
        )
        return

    try:
        run_analysis(incident_id)
    except Incident.DoesNotExist:
        logger.warning("analyze_incident: incident %s no longer exists", incident_id)
    except AnalysisFormatError as exc:
        # Already recorded as status=failed by the service.
        logger.warning("analyze_incident: giving up on incident %s: %s", incident_id, exc)
    except (RateLimitError, OpenRouterAPIError) as exc:
        if self.request.retries >= settings.AI_RETRY_MAX_ATTEMPTS:
            logger.error(
                "analyze_incident: retries exhausted for incident %s: %s",
                incident_id,
                exc,
            )
            mark_analysis_failed(incident_id, reason=f"retries exhausted: {exc}")
            return
        raise self.retry(exc=exc, countdown=retry_countdown(self.request.retries))
    except Exception:
        logger.exception(
            "analyze_incident: unexpected error for incident %s", incident_id
        )
        mark_analysis_failed(incident_id, reason="unexpected task error")
    else:
        # Phase 3A: the analysis landed — push it live so the frontend can
        # swap out its pending state without polling. Best-effort.
        publish_analysis_ready(incident_id)
