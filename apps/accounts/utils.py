import hashlib
import secrets


def generate_otp() -> str:
    """Six-digit code, zero-padded."""
    return f"{secrets.randbelow(1000000):06d}"


def generate_registration_token() -> str:
    """A 64-hex-char raw registration token, shown to the client exactly once."""
    return secrets.token_hex(32)


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()