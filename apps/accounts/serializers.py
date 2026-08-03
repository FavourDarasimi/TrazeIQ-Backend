from rest_framework import serializers

from .models import OTPPurpose
from .validators import validate_new_password


def validate_email_lower(value: str) -> str:
    return (value or "").strip().lower()


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_email(self, value):
        return validate_email_lower(value)

    def validate_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError(
                "Password must be at least 8 characters."
            )
        return validate_new_password(value)


class UserOutSerializer(serializers.Serializer):
    """Read-only shape of a user for response bodies."""

    email = serializers.EmailField(read_only=True)
    name = serializers.SerializerMethodField()
    email_verified = serializers.BooleanField(read_only=True)
    auth_provider = serializers.CharField(read_only=True)

    def get_name(self, obj) -> str:
        return obj.first_name or obj.email.split("@")[0]


class DetailResponseSerializer(serializers.Serializer):
    """Generic message body: ``{"detail": "<message>"}``."""

    detail = serializers.CharField()


class AuthSessionSerializer(serializers.Serializer):
    """Signed-in response. The JWT access + refresh tokens are set as httpOnly
    cookies, never returned in this body."""

    user = UserOutSerializer()


class VerifyEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)

    def validate_email(self, value):
        return validate_email_lower(value)


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices)

    def validate_email(self, value):
        return validate_email_lower(value)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return validate_email_lower(value)


class GoogleAuthSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(required=False, allow_blank=True, default="")
    id_token = serializers.CharField(
        required=False, allow_blank=True, write_only=True, default=""
    )

    def validate_email(self, value):
        return validate_email_lower(value)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return validate_email_lower(value)


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return validate_email_lower(value)

    def validate_new_password(self, value):
        return validate_new_password(value)