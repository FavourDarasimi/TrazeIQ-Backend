from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from apps.alerts.services import enqueue_alert_evaluation
from apps.incidents.models import Incident
from apps.realtime.services import publish_incident_event

import logging
import time

from .models import ErrorGroup, Event
from .utils import (
    fingerprint,
    first_line,
    redact_secrets,
    severity_from_level,
)

logger = logging.getLogger(__name__)

# Phase 5E: bounded retries for the race-safe upserts. Concurrent
# first-ingests collide on the unique keys; under SQLite/WAL the explicit
# transaction can also hit SQLITE_BUSY_SNAPSHOT (no busy-timeout applies).
# Postgres serializes these correctly, but the retry loop is harmless there
# and also covers deadlock errors (40P01).
_GROUP_UPSERT_ATTEMPTS = 3
_GROUP_UPSERT_RETRY_BASE_SECONDS = 0.05


def _upsert_error_group(project, fp, title, now):
    """Get-or-create the ErrorGroup, tolerating concurrent first-ingests."""
    for attempt in range(_GROUP_UPSERT_ATTEMPTS):
        try:
            with transaction.atomic():
                return ErrorGroup.objects.get_or_create(
                    project=project,
                    fingerprint=fp,
                    defaults={
                        "title": title,
                        "count": 0,
                        "first_seen": now,
                        "last_seen": now,
                    },
                )
        except IntegrityError:
            # A concurrent first-ingest won the race — reuse its group.
            try:
                return (
                    ErrorGroup.objects.get(project=project, fingerprint=fp),
                    False,
                )
            except ErrorGroup.DoesNotExist:
                pass  # winner rolled back — retry below
        except OperationalError:
            pass  # lock/snapshot conflict — retry below
        time.sleep(_GROUP_UPSERT_RETRY_BASE_SECONDS * (2**attempt))
    # Final attempt: let any error propagate (500) instead of looping forever.
    with transaction.atomic():
        return ErrorGroup.objects.get_or_create(
            project=project,
            fingerprint=fp,
            defaults={
                "title": title,
                "count": 0,
                "first_seen": now,
                "last_seen": now,
            },
        )


def _reuse_open_incident(group, project, level):
    """Reuse the group's open Incident, tolerating concurrent creation."""
    for attempt in range(_GROUP_UPSERT_ATTEMPTS):
        try:
            return Incident.objects.get_or_create(
                error_group=group,
                project=project,
                status=Incident.Status.OPEN,
                defaults={"severity": severity_from_level(level)},
            )
        except IntegrityError:
            # Lost the race for the open incident
            # (`uniq_open_incident_per_group`) — reuse the winner.
            try:
                return (
                    Incident.objects.get(
                        error_group=group, status=Incident.Status.OPEN
                    ),
                    False,
                )
            except Incident.DoesNotExist:
                pass  # winner rolled back — retry below
        except OperationalError:
            pass  # lock/snapshot conflict — retry below
        time.sleep(_GROUP_UPSERT_RETRY_BASE_SECONDS * (2**attempt))
    return Incident.objects.get_or_create(
        error_group=group,
        project=project,
        status=Incident.Status.OPEN,
        defaults={"severity": severity_from_level(level)},
    )


def ingest_event(
    project,
    *,
    message: str,
    stacktrace: str = "",
    level: str = Event.Level.ERROR,
    environment: str = "",
    service: str = "",
    endpoint: str = "",
    request_method: str = "",
    user_id: str = "",
    ip_address: str = "",
    metadata: dict | None = None,
    breadcrumbs: list | None = None,
) -> Event:
    """Persist one raw error occurrence and keep the groups/incidents correct.

    Order matters (spec §6):
      1. redact secrets, then
      2. fingerprint the redacted text,
      3. get-or-create the ErrorGroup for ``(project, fingerprint)`` and bump
         count/last_seen,
      4. reuse the open Incident for that group (create one if none),
      5. always persist the raw Event,
      6. decide + run AI analysis inline (new incident or stale analysis
          only) — no worker queue, so a slow model call delays the ingestion
          response; an analysis failure never fails the request.
    """
    # Phase 5E: structured ingest-latency logging for observability. Timing
    # only — it never changes control flow and never fails the request.
    _start = time.monotonic()
    redacted_message = redact_secrets(message)
    redacted_stacktrace = redact_secrets(stacktrace or "")
    fp = fingerprint(message=redacted_message, stacktrace=redacted_stacktrace)
    now = timezone.now()

    group, _group_created = _upsert_error_group(
        project, fp, first_line(redacted_message), now
    )

    ErrorGroup.objects.filter(pk=group.pk).update(
        count=F("count") + 1, last_seen=now
    )

    incident, _created = _reuse_open_incident(group, project, level)

    if _created:
        from apps.notifications.services import notify_incident_created

        # Best-effort inbox fan-out to the org — never fails ingestion.
        notify_incident_created(incident)

    event = Event.objects.create(
        project=project,
        error_group=group,
        message=redacted_message,
        stacktrace=redacted_stacktrace,
        level=level,
        environment=environment or project.environment,
        service=service,
        endpoint=endpoint,
        request_method=request_method,
        user_id=user_id,
        ip_address=ip_address,
        metadata=metadata or {},
        breadcrumbs=breadcrumbs or [],
        fingerprint=fp,
    )

    # Phase 3A: push the incident lifecycle event live. Best-effort — a
    # misconfigured/unreachable Pusher must not fail the ingestion request.
    publish_incident_event(
        incident,
        event_name="incident.created" if _created else "incident.updated",
        event=event,
    )

    # Phase 4C: alert evaluation runs inline from the same point where the
    # lifecycle event fires; cooldown enforcement prevents alert storms.
    # Best-effort like the pusher call — never allowed to fail ingestion.
    enqueue_alert_evaluation(incident.pk)

    logger.info(
        "event ingested project=%s fingerprint=%s latency_ms=%d new_incident=%s",
        project.pk,
        fp,
        int((time.monotonic() - _start) * 1000),
        _created,
    )

    return event