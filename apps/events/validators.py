from django.conf import settings
from rest_framework.exceptions import APIException


class PayloadTooLarge(APIException):
    """413 response for event payloads over the ingestion cap."""

    status_code = 413
    default_detail = "Payload too large."
    default_code = "PAYLOAD_TOO_LARGE"


def validate_payload_size(message: str, stacktrace: str) -> None:
    """Reject message+stacktrace payloads over ``EVENT_MAX_PAYLOAD_BYTES``.

    Runs in the serializer before anything reaches the DB — oversized
    payloads are dropped at the door (see Security-and-Scalability-Checklist:
    cap payload size).
    """
    cap = settings.EVENT_MAX_PAYLOAD_BYTES
    if len(message) + len(stacktrace or "") > cap:
        raise PayloadTooLarge(
            f"Payload exceeds the {cap:,} byte limit. Reduce the message or "
            "stacktrace size."
        )