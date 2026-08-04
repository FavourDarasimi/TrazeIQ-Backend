from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.incidents.models import Incident

from .models import ErrorGroup, Event
from .utils import (
    fingerprint,
    first_line,
    redact_secrets,
    severity_from_level,
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
) -> Event:
    """Persist one raw error occurrence and keep the groups/incidents correct.

    Order matters (spec §6):
      1. redact secrets, then
      2. fingerprint the redacted text,
      3. get-or-create the ErrorGroup for ``(project, fingerprint)`` and bump
         count/last_seen,
      4. reuse the open Incident for that group (create one if none),
      5. always persist the raw Event.

    Nothing here touches AI or async code — ingestion is purely synchronous
    and fast (Agent.md constraint 1 caps what this path is allowed to do).
    """
    redacted_message = redact_secrets(message)
    redacted_stacktrace = redact_secrets(stacktrace or "")
    fp = fingerprint(message=redacted_message, stacktrace=redacted_stacktrace)
    now = timezone.now()

    try:
        with transaction.atomic():
            group, _created = ErrorGroup.objects.get_or_create(
                project=project,
                fingerprint=fp,
                defaults={
                    "title": first_line(redacted_message),
                    "count": 0,
                    "first_seen": now,
                    "last_seen": now,
                },
            )
    except IntegrityError:
        # A concurrent first-ingest won the race — reuse its group.
        group = ErrorGroup.objects.get(project=project, fingerprint=fp)

    ErrorGroup.objects.filter(pk=group.pk).update(
        count=F("count") + 1, last_seen=now
    )

    incident, _created = Incident.objects.get_or_create(
        error_group=group,
        project=project,
        status=Incident.Status.OPEN,
        defaults={"severity": severity_from_level(level)},
    )

    return Event.objects.create(
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
        fingerprint=fp,
    )