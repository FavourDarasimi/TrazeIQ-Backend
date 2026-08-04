import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

from .utils import hash_code


class UserManager(BaseUserManager):
    """Email-based user manager — the username field is not used."""

    use_in_migrations = True

    def _create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The email address is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """The TrazeIQ user. Login identifier is email, not username."""

    username = None

    email = models.EmailField("email address", unique=True)
    email_verified = models.BooleanField(default=False)

    auth_provider = models.CharField(
        max_length=20,
        choices=[("email", "email"), ("google", "google")],
        default="email",
    )
    google_sub = models.CharField(max_length=255, unique=True, null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class OTPPurpose(models.TextChoices):
    EMAIL_VERIFICATION = "email_verification", "email_verification"
    PASSWORD_RESET = "password_reset", "password_reset"


class OTPCode(models.Model):
    """A 6-digit verification code, stored only as a hash.

    Delivery is email-first. While ``settings.AUTH_DEV_OTP`` is set (e.g.
    ``000000``), that code is additionally accepted so the flow works before
    real email delivery is wired up.

    Two keying modes:

    - ``user`` set (password reset): belongs to an existing account.
    - ``user`` None + ``email`` set (registration): the address is not an
      account yet — the code is only held until the signup completes.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="otp_codes",
        null=True,
        blank=True,
    )
    email = models.EmailField(null=True, blank=True, db_index=True)
    purpose = models.CharField(max_length=32, choices=OTPPurpose.choices)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "purpose"]),
            models.Index(fields=["email", "purpose"]),
        ]

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def matches(self, code: str) -> bool:
        dev_otp = getattr(settings, "AUTH_DEV_OTP", "")
        if dev_otp and secrets.compare_digest(code, dev_otp):
            return True
        return secrets.compare_digest(self.code_hash, hash_code(code))


class RegistrationToken(models.Model):
    """Single-use token minted after the OTP verifies, consumed at signup.

    Only the hash is stored; the raw token is shown to the client exactly once
    in the verify-otp response and must be presented to complete the signup.
    Expires after ``AUTH_REGISTRATION_TOKEN_TTL_MINUTES``.
    """

    email = models.EmailField(unique=True)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at
