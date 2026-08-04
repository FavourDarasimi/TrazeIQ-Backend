"""Unified API envelope.

Every TrazeIQ API response is the same shape:

- Success: ``{success: true, message: "<text>", data: <payload>}``
- Failure: ``{success: false, message: "<text>", error: {"code": "...", "fields": {...}?}}``

Views build responses with :func:`api_success` / :func:`api_error`. Anything
raised inside DRF (validation, auth, permissions, not-found, throttling ...)
is normalized into the same shape by :func:`drf_exception_handler`, wired as
``REST_FRAMEWORK["EXCEPTION_HANDLER"]``.
"""

import logging

from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.views import exception_handler as _drf_exception_handler

logger = logging.getLogger(__name__)


class ErrorCode:
    """Machine-readable error codes the frontend can branch on."""

    EMAIL_TAKEN = "EMAIL_TAKEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    ALREADY_VERIFIED = "ALREADY_VERIFIED"
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    OTP_USED = "OTP_USED"
    OTP_TOO_MANY_ATTEMPTS = "OTP_TOO_MANY_ATTEMPTS"
    OTP_MISSING = "OTP_MISSING"
    REGISTRATION_TOKEN_INVALID = "REGISTRATION_TOKEN_INVALID"
    REGISTRATION_TOKEN_EXPIRED = "REGISTRATION_TOKEN_EXPIRED"
    REFRESH_TOKEN_INVALID = "REFRESH_TOKEN_INVALID"
    GOOGLE_AUTH_FAILED = "GOOGLE_AUTH_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    @classmethod
    def otp(cls, reason: str) -> str:
        """Map an OTP consumer reason to its canonical error code."""
        return {
            "used": cls.OTP_USED,
            "expired": cls.OTP_EXPIRED,
            "too_many_attempts": cls.OTP_TOO_MANY_ATTEMPTS,
            "invalid": cls.OTP_INVALID,
        }.get(reason, cls.OTP_INVALID)


_DEFAULT_SUCCESS_MESSAGES = {
    http_status.HTTP_200_OK: "OK",
    http_status.HTTP_201_CREATED: "Created",
    http_status.HTTP_202_ACCEPTED: "Accepted",
}


def _success_message(message: str | None, status_code: int) -> str:
    if message:
        return message
    return _DEFAULT_SUCCESS_MESSAGES.get(status_code, "OK")


def api_success(
    data=None,
    message: str | None = None,
    status: int = http_status.HTTP_200_OK,
) -> Response:
    """Build a ``{success, message, data}`` response."""
    return Response(
        {
            "success": True,
            "message": _success_message(message, status),
            "data": data,
        },
        status=status,
    )


def api_error(
    code: str,
    message: str,
    status: int = http_status.HTTP_400_BAD_REQUEST,
    fields: dict | None = None,
) -> Response:
    """Build a ``{success, message, error: {code, fields?}}`` response."""
    error: dict = {"code": code}
    if fields:
        error["fields"] = fields
    return Response(
        {
            "success": False,
            "message": message,
            "error": error,
        },
        status=status,
    )


def _plain_text(detail) -> str | None:
    """Extract a human string from DRF error detail when one exists."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and isinstance(detail.get("detail"), str):
        return detail["detail"]
    return None


def _normalize_fields(detail) -> dict:
    """Flatten DRF validation detail into ``{field: [messages]}``."""
    if isinstance(detail, dict):
        return {
            str(key): [str(item) for item in value]
            if isinstance(value, (list, tuple))
            else [str(value)]
            for key, value in detail.items()
        }
    return {"detail": [str(detail)]}


STATUS_CODE_MAP = [
    (http_status.HTTP_401_UNAUTHORIZED, ErrorCode.NOT_AUTHENTICATED),
    (http_status.HTTP_403_FORBIDDEN, ErrorCode.PERMISSION_DENIED),
    (http_status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND),
    (http_status.HTTP_405_METHOD_NOT_ALLOWED, ErrorCode.METHOD_NOT_ALLOWED),
    (http_status.HTTP_429_TOO_MANY_REQUESTS, ErrorCode.TOO_MANY_REQUESTS),
]

_ERROR_FALLBACK_MESSAGES = {
    ErrorCode.NOT_AUTHENTICATED: "Authentication is required.",
    ErrorCode.PERMISSION_DENIED: "You do not have permission to do this.",
    ErrorCode.NOT_FOUND: "Not found.",
    ErrorCode.METHOD_NOT_ALLOWED: "Method not allowed.",
    ErrorCode.TOO_MANY_REQUESTS: "Too many requests. Try again shortly.",
}


def drf_exception_handler(exc, context):
    """Convert any DRF-raised exception into the unified error envelope."""
    response = _drf_exception_handler(exc, context)

    if response is None:
        # Unhandled outside DRF — never let a raw exception become the body.
        logger.exception("Unhandled API exception: %s", exc)
        return api_error(
            ErrorCode.INTERNAL_ERROR,
            "An unexpected error occurred.",
            status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    status_code = response.status_code
    detail = response.data

    for code, error_code in STATUS_CODE_MAP:
        if status_code == code:
            message = _plain_text(detail) or _ERROR_FALLBACK_MESSAGES[error_code]
            return api_error(error_code, message, status=status_code)

    # 400 validation errors carry the offending fields for inline UI messages.
    if status_code == http_status.HTTP_400_BAD_REQUEST:
        return api_error(
            ErrorCode.VALIDATION_FAILED,
            "Validation failed.",
            status=status_code,
            fields=_normalize_fields(detail),
        )

    # Any other DRF error keeps its status; the code falls back to the
    # exception's own code authoring, or a generic token.
    exc_code = getattr(exc, "default_code", "ERROR")
    return api_error(
        str(exc_code or "ERROR").upper(),
        _plain_text(detail) or str(detail),
        status=status_code,
    )


def envelope_schema(name: str, *, payload=None, error: bool = False):
    """Return a drf-spectacular inline serializer describing the envelope.

    Use inside ``@extend_schema(responses={...})`` so Swagger mirrors the
    real response shape. ``payload`` is the serializer for the ``data`` slot
    (``None`` on error responses, which instead describe ``error.code``).
    """
    from drf_spectacular.utils import inline_serializer

    from rest_framework import serializers

    error_schema = inline_serializer(
        f"{name}Error",
        fields={
            "code": serializers.CharField(),
            "fields": serializers.DictField(
                child=serializers.ListField(child=serializers.CharField()),
                required=False,
                allow_null=True,
            ),
        },
    )

    fields = {
        "success": serializers.BooleanField(),
        "message": serializers.CharField(),
        "error": error_schema,
    } if error else {
        "success": serializers.BooleanField(),
        "message": serializers.CharField(),
        "data": payload or serializers.JSONField(allow_null=True),
    }

    return inline_serializer(name, fields=fields)