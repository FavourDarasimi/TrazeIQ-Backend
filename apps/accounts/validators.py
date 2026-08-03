from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from rest_framework import serializers


def validate_new_password(value: str) -> str:
    try:
        validate_password(value)
    except ValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc
    return value