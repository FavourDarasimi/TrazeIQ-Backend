"""Thin wrapper around the Pusher SDK.

The secret key lives only here (read from settings) and in the channel-auth
endpoint — it must never leak into API responses or frontend code. Publishing
is best-effort and never raises: unconfigured credentials or a slow/unreachable
Pusher simply degrade to no-ops so the ingestion hot path stays fast.
"""

import logging

from django.conf import settings
from pusher import Pusher

logger = logging.getLogger(__name__)


def get_pusher() -> Pusher | None:
    """The configured client, or ``None`` when Pusher is not configured
    (empty app id/key/secret) — callers treat ``None`` as "publish nowhere"."""
    if not (settings.PUSHER_APP_ID and settings.PUSHER_KEY and settings.PUSHER_SECRET):
        return None
    return Pusher(
        app_id=settings.PUSHER_APP_ID,
        key=settings.PUSHER_KEY,
        secret=settings.PUSHER_SECRET,
        cluster=settings.PUSHER_CLUSTER,
        ssl=settings.PUSHER_USE_TLS,
        timeout=settings.PUSHER_PUBLISH_TIMEOUT_SECONDS,
    )


def publish(channel: str, event: str, payload: dict) -> bool:
    """Fire one event at ``channel``. Returns False (silently) when Pusher is
    unconfigured or the trigger fails — the caller must not fail because of it."""
    client = get_pusher()
    if client is None:
        return False
    try:
        client.trigger(channel, event, payload)
        return True
    except Exception as exc:  # noqa: BLE001 — publishing must never break the request
        logger.warning("pusher publish failed on %s/%s: %s", channel, event, exc)
        return False


def authenticate_channel(channel_name: str, socket_id: str) -> str:
    """Sign a private-channel auth response for a client that holds a socket.

    The caller is responsible for verifying the user actually has access to
    the project encoded in ``channel_name`` before calling this — the
    signature is only as good as that check."""
    client = get_pusher()
    if client is None:
        raise PusherUnavailable("Pusher is not configured.")
    return client.authenticate(channel=channel_name, socket_id=socket_id)


class PusherUnavailable(Exception):
    """Raised by :func:`authenticate_channel` when credentials are missing."""
