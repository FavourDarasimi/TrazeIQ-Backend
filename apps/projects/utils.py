import hashlib
import hmac
import secrets

from django.conf import settings

_API_KEY_SECRET = None


def _hash_secret() -> bytes:
    """HMAC key for API keys — env override, falling back to SECRET_KEY."""
    global _API_KEY_SECRET
    if _API_KEY_SECRET is None:
        configured = getattr(settings, "API_KEY_HASH_SECRET", "")
        _API_KEY_SECRET = (configured or settings.SECRET_KEY).encode()
    return _API_KEY_SECRET


def generate_api_key() -> str:
    """A 64-hex-char raw API key, shown to the creator exactly once."""
    return secrets.token_hex(32)


def hash_api_key(raw_key: str) -> str:
    """Deterministic HMAC-SHA256 digest, safe to store and look up by."""
    return hmac.new(
        _hash_secret(), raw_key.encode(), hashlib.sha256
    ).hexdigest()


def api_key_prefix(raw_key: str) -> str:
    """A short, non-secret preview of the key for display in the UI."""
    return raw_key[:8]