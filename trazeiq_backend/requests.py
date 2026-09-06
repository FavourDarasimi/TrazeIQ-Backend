"""Shared request-body helpers.

Every TrazeIQ write endpoint expects a JSON *object*. A bare array, string
or null body parses fine in DRF but crashes direct ``request.data.get``
access with ``AttributeError`` (500). :func:`body_as_dict` is the one
isinstance guard all such endpoints share: non-object bodies fail fast
with a 400 validation error instead of a 500.

``QueryDict`` (form-data bodies) subclasses ``dict``, so multipart/form
posts keep working — only genuinely non-object JSON is rejected.
"""

from rest_framework import serializers


def body_as_dict(request) -> dict:
    """Return the request body as a dict, or raise 400 for non-objects.

    Safe to call from ``get_permissions`` / ``get_permission_org_id`` as
    well as handlers: ``serializers.ValidationError`` is an
    ``APIException``, so it renders as the standard 400 envelope from any
    of those positions.
    """
    data = request.data
    if not isinstance(data, dict):
        raise serializers.ValidationError(
            "Expected a JSON object as the request body."
        )
    return data
