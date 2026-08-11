"""Slack API client (Phase 4D) — OAuth token exchange and chat.postMessage.

Stdlib urllib only, like every other HTTP client in this project. The
connect view calls :func:`exchange_oauth_code`; the alert dispatcher calls
:func:`post_slack_message`. Failures surface as our own exceptions so
callers never see urllib internals.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


class SlackUnavailable(Exception):
    """Credentials missing — connect cannot proceed (→ 503)."""


class SlackAPIError(Exception):
    """Slack answered, but not with success (bad code, network, timeout)."""


def _request_json(url: str, *, method: str, fields: dict | None = None,
                  token: str | None = None, timeout: float | None = None) -> dict:
    timeout = timeout if timeout is not None else settings.SLACK_API_TIMEOUT_SECONDS
    data = None
    if fields is not None:
        data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SlackAPIError(f"Slack request failed: {exc}")
    return json.loads(body)


def exchange_oauth_code(code: str, redirect_uri: str | None = None) -> dict:
    """Exchange an OAuth ``code`` for an access token (``oauth.v2.access``).

    Returns ``{"access_token": ..., "team_name": ...}``. Raises
    :class:`SlackUnavailable` when the app is not configured and
    :class:`SlackAPIError` when Slack rejects the exchange.
    """
    if not settings.SLACK_CLIENT_ID or not settings.SLACK_CLIENT_SECRET:
        raise SlackUnavailable("SLACK_CLIENT_ID/SLACK_CLIENT_SECRET not configured")

    fields = {
        "client_id": settings.SLACK_CLIENT_ID,
        "client_secret": settings.SLACK_CLIENT_SECRET,
        "code": code,
    }
    if redirect_uri:
        fields["redirect_uri"] = redirect_uri
    data = _request_json(settings.SLACK_OAUTH_URL, method="POST", fields=fields)
    if not data.get("ok"):
        raise SlackAPIError(data.get("error", "unknown slack error"))
    team_name = ((data.get("team") or {}).get("name")) or ""
    return {"access_token": data["access_token"], "team_name": team_name}


def post_slack_message(*, token: str, channel: str, payload: dict,
                       timeout: float | None = None) -> None:
    """Send a message body to a channel with the bot token."""
    fields = {
        "channel": channel,
        "text": payload.get("text", ""),
    }
    if "blocks" in payload:
        fields["blocks"] = json.dumps(payload["blocks"])
    data = _request_json(
        settings.SLACK_CHAT_POST_URL,
        method="POST",
        fields=fields,
        token=token,
        timeout=timeout,
    )
    if not data.get("ok"):
        raise SlackAPIError(data.get("error", "unknown slack error"))