import hashlib
import re

# ---- Secret redaction (Agent.md rule 5: error content is untrusted input) ----

_SECRET_KEY_RE = re.compile(r"(?i)\bsecret\s*_?\s*key\s*=\s*['\"]?[^\s'\"]+")
_DATABASE_URL_RE = re.compile(r"(?i)\bdatabase\s*_?\s*url\s*=\s*['\"]?[^\s'\"]+")
_PASSWORD_RE = re.compile(r"(?i)(password\s*[:=]\s*['\"])([^'\"]+)(['\"])")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")
_GENERIC_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|client[_-]?secret)\s*[:=]\s*['\"]?[^\s'\"]+"
)

_REDACTIONS = [
    (_SECRET_KEY_RE, "SECRET_KEY=[REDACTED]"),
    (_DATABASE_URL_RE, "DATABASE_URL=[REDACTED]"),
    (_PASSWORD_RE, r"\1[REDACTED]\3"),
    (_BEARER_RE, "Bearer [REDACTED]"),
    (_JWT_RE, "[REDACTED-JWT]"),
    (_GENERIC_CREDENTIAL_RE, r"\1=[REDACTED]"),
]


def redact_secrets(text: str) -> str:
    """Scrub likely secrets from error content before it is stored or analyzed.

    Runs before anything touches the DB or (later) the AI prompt — a
    monitoring tool must never become the leak vector for credentials that
    were accidentally logged into a stacktrace.
    """
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


# ---- Fingerprinting (Agent.md rule 3: dedup before you store) ----

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]+")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
_LINE_COL_RE = re.compile(r":\s*\d+(?::\s*\d+)?")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    text = _ADDRESS_RE.sub("0xADDR", text)
    text = _UUID_RE.sub("UUID", text)
    text = _LINE_COL_RE.sub(":N", text)
    text = _WS_RE.sub(" ", text)
    return text.strip().lower()


def fingerprint(*, message: str, stacktrace: str = "") -> str:
    """Deterministic signature for one error pattern, independent of line
    numbers, memory addresses and UUIDs.

    Built from the normalized first message line (the error type) plus the
    top five normalized stack frames — the parts that identify the pattern
    without the noise that varies per occurrence.
    """
    frames = [
        line.strip()
        for line in (stacktrace or "").splitlines()
        if line.strip()
    ]
    parts = [_normalize(message.splitlines()[0])] if message else []
    parts += [_normalize(frame) for frame in frames[:5]]
    payload = "\n".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()


def first_line(text: str) -> str:
    """The message line used as the ErrorGroup title."""
    line = text.splitlines()[0] if text else ""
    return line[:255]


def severity_from_level(level: str) -> str:
    """Map an event ``level`` to the incident ``severity`` scale."""
    return {
        "fatal": "critical",
        "error": "high",
        "warning": "medium",
        "info": "low",
        "debug": "low",
    }.get(level, "medium")


def level_from_severity(severity: str) -> str:
    """Map an incident ``severity`` back to the equivalent event ``level``.

    One-to-one inverse of :func:`severity_from_level` — used for the
    ``?severity=`` filter on the events list.
    """
    return {
        "critical": "fatal",
        "high": "error",
        "medium": "warning",
        "low": "info",
    }.get(severity, severity)