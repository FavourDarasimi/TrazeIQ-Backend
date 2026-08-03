import hashlib
import secrets


def generate_otp() -> str:
    """Six-digit code, zero-padded."""
    return f"{secrets.randbelow(1000000):06d}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()