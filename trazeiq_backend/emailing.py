"""Email-delivery helpers — one place that knows what "configured" means.

The console/dummy backends never deliver mail: they report success while
the message goes to stdout (console) or nowhere (dummy). With one of those
active, signup OTP codes, password resets and email alerts silently go
nowhere — the API still returns "sent" and alert logs read "dispatched".
Every sending path consults :func:`email_configured` so unconfigured
delivery fails loudly instead of fake-succeeding.
"""

from django.conf import settings

# Backend substrings that never deliver a message, whatever else is set.
_NON_DELIVERING_BACKENDS = ("console", "dummy")


def email_backend() -> str:
    """The configured ``EMAIL_BACKEND`` path (empty string if unset)."""
    return getattr(settings, "EMAIL_BACKEND", "") or ""


def email_configured() -> bool:
    """Whether sending mail can actually deliver anywhere.

    Console/dummy backends never deliver (locmem does capture, so tests
    using it count as configured). Whether an SMTP host is reachable is a
    runtime concern — this answers only "is delivery even possible".
    """
    backend = email_backend().lower()
    return not any(marker in backend for marker in _NON_DELIVERING_BACKENDS)
