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


def _upsert_error_group(project, fp, title, now, fingerprint_version=2):
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
                        "fingerprint_version": fingerprint_version,
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
                "fingerprint_version": fingerprint_version,
            },
        )


def _reuse_open_incident(group, project, level, release="", endpoint=""):
    """Reuse the group's active Incident, or auto-reopen a resolved/fix_verified one."""
    # 1. Reuse existing open or investigating incident
    active = (
        Incident.objects.filter(
            error_group=group,
            status__in=[Incident.Status.OPEN, Incident.Status.INVESTIGATING],
        )
        .order_by("-created_at")
        .first()
    )
    if active is not None:
        return (active, False)

    # 2. Reopen a previously resolved or fix_verified incident when error returns
    past_incident = (
        Incident.objects.filter(
            error_group=group,
            status__in=[Incident.Status.RESOLVED, Incident.Status.FIX_VERIFIED],
        )
        .order_by("-created_at")
        .first()
    )
    if past_incident is not None:
        past_incident.status = Incident.Status.OPEN
        past_incident.resolved_at = None
        past_incident.save(update_fields=["status", "resolved_at"])

        rel_text = f" in release {release}" if release else ""
        ep_text = f" (endpoint: {endpoint})" if endpoint else ""
        msg = f"Automatically reopened: error re-occurred{rel_text}{ep_text}"

        from apps.incidents.models import TimelineEntry
        TimelineEntry.objects.create(
            incident=past_incident,
            kind=TimelineEntry.Kind.STATUS_CHANGE,
            content=msg[:1000],
            actor=None,
        )

        from apps.notifications.services import notify_incident_updated
        notify_incident_updated(
            past_incident,
            actor_id=None,
            changes=["status → open", msg],
        )
        return (past_incident, False)

    # 3. First occurrence for this group: get-or-create open incident
    for attempt in range(_GROUP_UPSERT_ATTEMPTS):
        try:
            return Incident.objects.get_or_create(
                error_group=group,
                project=project,
                status=Incident.Status.OPEN,
                defaults={"severity": severity_from_level(level)},
            )
        except IntegrityError:
            try:
                return (
                    Incident.objects.get(
                        error_group=group, status=Incident.Status.OPEN
                    ),
                    False,
                )
            except Incident.DoesNotExist:
                pass
        except OperationalError:
            pass
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
    frames: list[dict] | None = None,
    level: str = Event.Level.ERROR,
    environment: str = "",
    service: str = "",
    endpoint: str = "",
    request_method: str = "",
    user_id: str = "",
    ip_address: str = "",
    release: str = "",
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
    fp = fingerprint(
        message=redacted_message,
        stacktrace=redacted_stacktrace,
        frames=frames,
        version=2,
    )
    now = timezone.now()

    group, _group_created = _upsert_error_group(
        project, fp, first_line(redacted_message), now, fingerprint_version=2
    )

    ErrorGroup.objects.filter(pk=group.pk).update(
        count=F("count") + 1, last_seen=now
    )

    incident, _created = _reuse_open_incident(
        group, project, level, release=release, endpoint=endpoint
    )

    if _created:
        from apps.notifications.services import notify_incident_created

        # Best-effort inbox fan-out to the org — never fails ingestion.
        notify_incident_created(incident)

    meta = dict(metadata or {})
    if frames and "frames" not in meta:
        meta["frames"] = frames

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
        release=release,
        metadata=meta,
        breadcrumbs=breadcrumbs or [],
        fingerprint=fp,
        fingerprint_version=2,
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


def regroup_project_error_groups(
    project,
    actor=None,
    dry_run: bool = False,
) -> dict:
    """Regroup all events in a project according to the v2 fingerprint algorithm.

    Consolidates split error groups caused by wrapper functions, differing
    middleware depths, or changed line numbers into canonical v2 ErrorGroups.
    """
    from apps.auditlog.models import AuditAction
    from apps.auditlog.services import record_audit_log

    events = list(
        Event.objects.filter(project=project).order_by("created_at")
    )
    groups_before = ErrorGroup.objects.filter(project=project).count()
    total_events = len(events)

    if not events:
        return {
            "dry_run": dry_run,
            "project_id": str(project.id),
            "events_reprocessed": 0,
            "groups_before": groups_before,
            "groups_after": groups_before,
            "groups_consolidated": 0,
        }

    # Map each event to its new v2 fingerprint
    event_fp_map: dict[str, str] = {}
    fps_to_events: dict[str, list[Event]] = {}
    for ev in events:
        frames = None
        if isinstance(ev.metadata, dict):
            frames = ev.metadata.get("frames")
        new_fp = fingerprint(
            message=ev.message,
            stacktrace=ev.stacktrace,
            frames=frames,
            version=2,
        )
        event_fp_map[str(ev.id)] = new_fp
        fps_to_events.setdefault(new_fp, []).append(ev)

    groups_after = len(fps_to_events)
    consolidated = max(0, groups_before - groups_after)

    if dry_run:
        return {
            "dry_run": True,
            "project_id": str(project.id),
            "events_reprocessed": total_events,
            "groups_before": groups_before,
            "groups_after": groups_after,
            "groups_consolidated": consolidated,
        }

    with transaction.atomic():
        canonical_group_ids = []
        for fp, group_events in fps_to_events.items():
            first_event = group_events[0]
            last_event = group_events[-1]

            canonical_group = ErrorGroup.objects.filter(
                project=project, fingerprint=fp
            ).first()

            if canonical_group is None:
                canonical_group = ErrorGroup.objects.create(
                    project=project,
                    fingerprint=fp,
                    fingerprint_version=2,
                    title=first_line(first_event.message),
                    count=len(group_events),
                    first_seen=first_event.created_at,
                    last_seen=last_event.created_at,
                )
            else:
                canonical_group.fingerprint_version = 2
                canonical_group.count = len(group_events)
                canonical_group.first_seen = first_event.created_at
                canonical_group.last_seen = last_event.created_at
                canonical_group.save(
                    update_fields=[
                        "fingerprint_version",
                        "count",
                        "first_seen",
                        "last_seen",
                    ]
                )

            canonical_group_ids.append(canonical_group.id)

            # Reassign events
            event_ids = [e.id for e in group_events]
            Event.objects.filter(id__in=event_ids).update(
                error_group=canonical_group,
                fingerprint=fp,
                fingerprint_version=2,
            )

            # Gather all old groups that previously owned these events
            old_group_ids = {
                e.error_group_id
                for e in group_events
                if e.error_group_id and e.error_group_id != canonical_group.id
            }

            if old_group_ids:
                # Merge open incidents safely (preserving uniq_open_incident_per_group)
                open_incidents = list(
                    Incident.objects.filter(
                        error_group_id__in=old_group_ids | {canonical_group.id},
                        status=Incident.Status.OPEN,
                    ).order_by("created_at")
                )

                if open_incidents:
                    primary_open = open_incidents[0]
                    primary_open.error_group = canonical_group
                    primary_open.save(update_fields=["error_group"])

                    # Extra open incidents get resolved to avoid duplicate open incidents
                    for duplicate_open in open_incidents[1:]:
                        duplicate_open.error_group = canonical_group
                        duplicate_open.status = Incident.Status.RESOLVED
                        duplicate_open.resolved_at = timezone.now()
                        duplicate_open.save(
                            update_fields=[
                                "error_group",
                                "status",
                                "resolved_at",
                            ]
                        )

                # Reassign non-open incidents to canonical_group
                Incident.objects.filter(
                    error_group_id__in=old_group_ids
                ).exclude(status=Incident.Status.OPEN).update(
                    error_group=canonical_group
                )

        # Delete any obsolete ErrorGroups that no longer contain events
        ErrorGroup.objects.filter(project=project).exclude(
            id__in=canonical_group_ids
        ).delete()

        if actor:
            record_audit_log(
                organization=project.organization,
                actor=actor,
                action=AuditAction.GROUPS_REGROUPED,
                target=(
                    f"Project {project.name}: {consolidated} error groups "
                    f"consolidated to fingerprint v2"
                ),
            )

    return {
        "dry_run": False,
        "project_id": str(project.id),
        "events_reprocessed": total_events,
        "groups_before": groups_before,
        "groups_after": len(canonical_group_ids),
        "groups_consolidated": consolidated,
    }