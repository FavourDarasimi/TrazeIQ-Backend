"""AI analysis orchestration — the business logic behind analyze_incident.

Two entry points, one per layer boundary:

- ``enqueue_analysis_if_needed`` — called synchronously from the ingestion
  path (spec §6 steps 8–9). Cheap DB check for the cache rule, then one
  ``.delay()``. Never raises: a broker outage must not fail ingestion.
- ``run_analysis`` — called by the Celery task. Does the LLM work: pending
  row, prompt, fallback chain, strict JSON parsing, persistence.

The model fallback chain and the LLM-level strict-reminder retry happen here;
Celery-level retries (429/5xx backoff) are the task's job.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from apps.events.models import Event
from apps.events.utils import redact_secrets
from apps.incidents.models import Incident, TimelineEntry

from . import openrouter
from .models import AIAnalysis
from .prompts import STRICT_REMINDER, SYSTEM_PROMPT, build_user_prompt
from .utils import parse_analysis_json

logger = logging.getLogger(__name__)

RECENT_WINDOW = timedelta(hours=1)

TIMELINE_CONTENT_MAX = 1000


class AnalysisFormatError(Exception):
    """The model answered twice but never produced strict JSON — give up."""


def retry_countdown(attempt: int) -> int:
    """Exponential backoff seconds for the 429 retry: base * 2**attempt."""
    return settings.AI_RETRY_BASE_SECONDS * (2 ** max(attempt, 0))


def latest_analysis_for_incident(incident_id: int) -> AIAnalysis | None:
    return (
        AIAnalysis.objects.filter(incident_id=incident_id)
        .order_by("-created_at", "-id")
        .first()
    )


def enqueue_analysis_if_needed(*, incident_id: int, is_new: bool) -> bool:
    """Decide whether this occurrence needs a fresh analysis, then enqueue.

    The cache rule (spec §7 — this is what keeps us inside OpenRouter's
    free-tier rate limits): only brand-new incidents, incidents whose latest
    analysis failed, or incidents whose last analysis is older than
    ``AI_ANALYSIS_CACHE_HOURS`` get a task. A pending analysis means a task
    is already in flight — skip.

    Returns True when a task was enqueued. The broker path is wrapped so a
    Redis outage degrades to "no analysis yet" instead of failing the
    ingestion request (Agent.md rule 1: ingestion never depends on the async
    pipeline).
    """
    if not is_new:
        latest = latest_analysis_for_incident(incident_id)
        if latest is not None:
            if latest.status == AIAnalysis.Status.PENDING:
                return False
            if latest.status == AIAnalysis.Status.READY:
                cutoff = timezone.now() - timedelta(
                    hours=settings.AI_ANALYSIS_CACHE_HOURS
                )
                if latest.created_at >= cutoff:
                    return False

    try:
        # Lazy import: tasks imports this module, so a module-level import
        # would be circular.
        from .tasks import analyze_incident

        analyze_incident.delay(incident_id)
    except Exception as exc:  # noqa: BLE001 — any broker failure is swallowed
        logger.warning(
            "Analysis enqueue failed for incident %s: %s", incident_id, exc
        )
        return False
    return True


def trigger_manual_analysis(*, incident: Incident) -> AIAnalysis:
    """Manually re-trigger AI analysis for an incident, bypassing the cache window.

    Creates a pending AIAnalysis row (or reuses an existing pending row) and
    enqueues the analyze_incident Celery task.
    """
    analysis = _pending_analysis(incident)
    try:
        from .tasks import analyze_incident

        analyze_incident.delay(incident.id)
    except Exception as exc:  # noqa: BLE001 — broker failure swallowed
        logger.warning(
            "Manual analysis enqueue failed for incident %s: %s",
            incident.id,
            exc,
        )
    return analysis



def _pending_analysis(incident: Incident) -> AIAnalysis:
    """The in-flight row for this incident — created if this is the first
    attempt. The partial unique constraint guarantees one pending row per
    incident, so a retried task reuses its own row instead of double-creating
    (Agent.md convention: Celery tasks are idempotent where practical)."""
    try:
        analysis, _created = AIAnalysis.objects.get_or_create(
            incident=incident, status=AIAnalysis.Status.PENDING
        )
    except IntegrityError:
        analysis = AIAnalysis.objects.get(
            incident=incident, status=AIAnalysis.Status.PENDING
        )
    return analysis


def _build_user_prompt(incident: Incident) -> str:
    """The prompt from the incident's most recent raw occurrence.

    The stored event is already redacted (Phase 1D), and it is redacted again
    here — belt and suspenders, since this text goes to a third-party model.
    """
    event = (
        Event.objects.filter(error_group=incident.error_group)
        .order_by("-created_at", "-id")
        .only("message", "stacktrace", "service", "environment")
        .first()
    )
    recent_count = Event.objects.filter(
        error_group=incident.error_group,
        created_at__gte=timezone.now() - RECENT_WINDOW,
    ).count()
    return build_user_prompt(
        message=redact_secrets(
            (event.message if event else "") or incident.error_group.title
        ),
        stacktrace=redact_secrets(event.stacktrace if event else ""),
        service=event.service if event else "",
        environment=event.environment if event else "",
        recent_count=recent_count,
    )


def _complete(analysis: AIAnalysis, *, model: str, parsed: dict, attempts: list) -> None:
    analysis.root_cause = parsed["root_cause"]
    analysis.suggested_fix = parsed["suggested_fix"]
    analysis.confidence = parsed["confidence"]
    analysis.model_used = model
    analysis.status = AIAnalysis.Status.READY
    analysis.raw_response = {"attempts": attempts}
    analysis.save(
        update_fields=[
            "root_cause",
            "suggested_fix",
            "confidence",
            "model_used",
            "status",
            "raw_response",
        ]
    )
    TimelineEntry.objects.create(
        incident_id=analysis.incident_id,
        kind=TimelineEntry.Kind.AI_ANALYSIS,
        content=(
            f"AI analysis ({parsed['confidence']} confidence): "
            f"{parsed['root_cause']} Suggested fix: {parsed['suggested_fix']}"
        )[:TIMELINE_CONTENT_MAX],
    )


def _fail(analysis: AIAnalysis, *, attempts: list, reason: str) -> None:
    analysis.status = AIAnalysis.Status.FAILED
    analysis.model_used = ""
    analysis.raw_response = {"attempts": attempts, "error": reason}
    analysis.save(update_fields=["status", "model_used", "raw_response"])


def mark_analysis_failed(incident_id: int, *, reason: str) -> None:
    """Flip the incident's pending analysis row to ``failed`` — the task's
    graceful give-up paths (no API key, retries exhausted, unexpected error).
    """
    analysis = (
        AIAnalysis.objects.filter(
            incident_id=incident_id, status=AIAnalysis.Status.PENDING
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if analysis is None:
        return
    raw = dict(analysis.raw_response or {})
    raw.setdefault("error", reason)
    analysis.status = AIAnalysis.Status.FAILED
    analysis.raw_response = raw
    analysis.save(update_fields=["status", "raw_response"])


def run_analysis(incident_id: int) -> AIAnalysis:
    """One analysis run for an incident: call the LLM, save the result.

    Raises :class:`RateLimitError` / :class:`OpenRouterAPIError` when every
    model failed with a retry-able error (the task backs off and retries),
    :class:`AnalysisFormatError` when the model answered but never produced
    strict JSON (the task gives up), and ``Incident.DoesNotExist`` when the
    incident is gone.
    """
    incident = Incident.objects.select_related("error_group").get(pk=incident_id)
    analysis = _pending_analysis(incident)
    user_prompt = _build_user_prompt(incident)

    attempts: list[dict] = []
    last_client_error: openrouter.OpenRouterError | None = None

    for model in settings.OPENROUTER_MODELS:
        for strict in (False, True):
            system_prompt = SYSTEM_PROMPT + (STRICT_REMINDER if strict else "")
            try:
                content = openrouter.call_openrouter(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                )
            except (openrouter.RateLimitError, openrouter.OpenRouterAPIError) as exc:
                attempts.append({"model": model, "error": str(exc)})
                last_client_error = exc
                # A call that never answered isn't strict-retried; the next
                # model in the fallback chain is.
                break
            attempts.append({"model": model, "content": content})
            parsed = parse_analysis_json(content)
            if parsed is not None:
                _complete(analysis, model=model, parsed=parsed, attempts=attempts)
                return analysis
        else:
            # The model answered twice but never in the strict shape — give up
            # gracefully (spec: retry once with a stricter reminder, then
            # mark failed rather than crashing the task).
            reason = f"Model {model} never returned strict JSON"
            _fail(analysis, attempts=attempts, reason=reason)
            raise AnalysisFormatError(reason)

    # Every model failed with a retry-able error — keep the row pending and
    # let the task back off and retry.
    analysis.raw_response = {"attempts": attempts}
    analysis.save(update_fields=["raw_response"])
    raise last_client_error
