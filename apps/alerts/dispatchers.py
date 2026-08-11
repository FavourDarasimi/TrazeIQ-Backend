"""Outbound alert dispatch (Phase 4D).

One entry point — :func:`dispatch` — which routes a rule+incident to the
right channel and raises :class:`DispatchError` on any failure so the
evaluation service can log a failed AlertLog. Every network call is
stdlib-urllib with a short timeout and no redirects (a redirect following a
validated public URL could land on an internal address — the SSRF defense
must not be redirectable away).
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    """Any failure delivering an alert — caller logs it, never crashes."""


def incident_link(incident) -> str:
    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/incidents/{incident.id}"


def _root_cause(incident) -> str | None:
    """The latest ready AI root cause, if one exists yet."""
    from apps.ai.models import AIAnalysis

    return (
        AIAnalysis.objects.filter(
            incident=incident, status=AIAnalysis.Status.READY
        )
        .order_by("-created_at")
        .values_list("root_cause", flat=True)
        .first()
    )


def _summary_payload(incident) -> dict:
    """The incident facts every channel renders (title, severity, link…)."""
    return {
        "title": incident.error_group.title,
        "severity": incident.severity,
        "status": incident.status,
        "link": incident_link(incident),
        "root_cause": _root_cause(incident),
        "occurrences": incident.error_group.count,
    }


def _post_json(url: str, payload: dict, *, timeout: float, token: str | None = None) -> None:
    """POST JSON to an already-validated URL. No redirects, short timeout."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DispatchError(f"Dispatch request failed: {exc}")
    return body


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow no redirects — a target validated as public must stay public."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "redirect blocked", headers, fp
        )


def _dispatch_email(rule, incident) -> None:
    payload = _summary_payload(incident)
    subject = f"[TrazeIQ] {payload['severity'].title()} incident: {payload['title']}"
    lines = [
        "TrazeIQ incident alert",
        "",
        payload["title"],
        f"Severity: {payload['severity']}",
        f"Status: {payload['status']}",
        f"Occurrences: {payload['occurrences']}",
        "",
        "Root cause: "
        + (payload["root_cause"] or "AI analysis pending"),
        "",
        f"View incident: {payload['link']}",
    ]
    send_mail(subject, "\n".join(lines), None, [rule.target])


def _slack_text_payload(payload: dict) -> dict:
    """Slack message body — both chat.postMessage and webhooks accept it."""
    cause = payload["root_cause"] or "AI analysis pending"
    text = (
        f"*{payload['severity'].title()} incident:* {payload['title']}\n"
        f"Status: *{payload['status']}* · Occurrences: {payload['occurrences']} · "
        f"Root cause: {cause}\n{payload['link']}"
    )
    return {
        "text": text,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{payload['severity'].title()} incident:* "
                        f"{payload['title']}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Status: *{payload['status']}* · "
                        f"Occurrences: {payload['occurrences']}\n"
                        f"Root cause: {cause}\n{payload['link']}"
                    ),
                },
            },
        ],
    }


def _dispatch_slack(rule, incident) -> None:
    payload = _summary_payload(incident)
    timeout = settings.DISPATCH_TIMEOUT_SECONDS
    target = rule.target.strip()
    if target.startswith(("http://", "https://")):
        # Incoming-webhook style: a plain POST to the validated URL.
        _post_json(target, _slack_text_payload(payload), timeout=timeout)
        return

    # Bot-token style: the rule's target is the channel name (#alerts).
    from apps.integrations.models import SlackIntegration
    from apps.integrations.slack import post_slack_message

    integration = SlackIntegration.objects.filter(
        organization=incident.project.organization_id
    ).first()
    if integration is None:
        raise DispatchError("No Slack workspace connected for this organization.")
    post_slack_message(
        token=integration.access_token,
        channel=target,
        payload=_slack_text_payload(payload),
        timeout=timeout,
    )


def _dispatch_webhook(rule, incident) -> None:
    payload = _summary_payload(incident)
    _post_json(rule.target, payload, timeout=settings.DISPATCH_TIMEOUT_SECONDS)


def dispatch(rule, incident) -> None:
    """Deliver one alert for a rule+incident pair. Raises DispatchError."""
    channel = rule.channel
    if channel == "email":
        _dispatch_email(rule, incident)
    elif channel == "slack":
        _dispatch_slack(rule, incident)
    elif channel == "webhook":
        _dispatch_webhook(rule, incident)
    else:
        raise DispatchError(f"Unknown channel: {channel}")