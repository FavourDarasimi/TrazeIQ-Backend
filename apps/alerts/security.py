"""SSRF defense for outbound alert dispatch (Phase 4D).

Every custom target URL (webhook channel, Slack webhook targets) is
validated before dispatch: the URL must be http/https and every IP the
hostname resolves to must be a public address. ``127.0.0.1``,
``169.254.169.254``, ``10.x.x.x``, ``::1``, ``0.0.0.0`` and friends are all
rejected — as are hostnames that merely *resolve* to such addresses, so
``localtest.me``-style aliases cannot sneak past a literal-IP check.

The same validator runs at rule creation (fail fast, with a clear message)
and again at dispatch time (defense in depth against a rule edited after
creation or DNS changes).
"""

import ipaddress
import logging
import socket
import urllib.parse

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")


def _is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _resolve_hostname(hostname: str) -> list[str]:
    """All IPs the hostname resolves to (empty when unresolvable)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError):
        return []
    return {info[4][0] for info in infos}


def validate_dispatch_target_url(url: str) -> str:
    """Return the URL unchanged when it is safe to dispatch to, else raise
    ``ValidationError`` with a human-readable reason."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        raise ValidationError("Target must be a valid http(s) URL.")
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValidationError("Target must be a valid http(s) URL.")

    hostname = parsed.hostname
    addresses = _resolve_hostname(hostname)
    if not addresses:
        raise ValidationError(f"Cannot resolve hostname: {hostname}")
    for address in addresses:
        if not _is_public_address(address):
            raise ValidationError(
                f"Target resolves to a private or reserved address "
                f"({address}) and is not allowed."
            )
    return url