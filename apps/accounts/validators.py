from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from rest_framework import serializers


validate_username_format = RegexValidator(
    regex=r"^[a-z0-9._-]+$",
    message=(
        "Username may only contain lowercase letters, digits, dot, "
        "underscore and hyphen."
    ),
)


def validate_username(value: str) -> str:
    """Enforce the public username rules: 3–30 chars, lowercase charset.

    Raises Django's ``ValidationError`` so it works both as a model-field
    validator and (converted automatically) inside DRF serializers.
    Callers normalize to lowercase before validating.
    """
    if not isinstance(value, str) or not 3 <= len(value) <= 30:
        raise ValidationError("Username must be between 3 and 30 characters.")
    validate_username_format(value)
    return value


def validate_new_password(value: str) -> str:
    try:
        validate_password(value)
    except ValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc
    return value