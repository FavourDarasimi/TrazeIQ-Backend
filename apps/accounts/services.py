import json
import logging
import urllib.request
from urllib.error import URLError

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import OTPCode, OTPPurpose, User
from .selectors import (
    get_live_otp,
    get_user_by_email,
    get_user_by_google_sub,
    get_user_by_id,
    user_exists,
)
from .utils import generate_otp, hash_code

logger = logging.getLogger(__name__)


# --- Registration / email verification ---------------------------------------

def register_user(email: str, password: str, name: str) -> User | None:
    """Create an inactive account and email a verification code.

    Returns ``None`` when the email is already taken.
    """
    if user_exists(email):
        return None

    user = User.objects.create_user(
        email=email,
        password=password,
        first_name=(name or "").strip(),
        is_active=False,
    )
    issue_otp(user, OTPPurpose.EMAIL_VERIFICATION)
    return user


def verify_email(email: str, code: str) -> tuple[bool, str, User | None]:
    """Activate a user once the emailed code is accepted.

    Returns ``(ok, reason, user)``; on failure ``user`` is ``None``.
    """
    user = get_user_by_email(email)
    if user is None:
        return False, "invalid", None

    otp = get_live_otp(user, OTPPurpose.EMAIL_VERIFICATION)
    if otp is None:
        return False, "invalid", None

    ok, reason = consume_otp(otp, code)
    if not ok:
        return False, reason, None

    user.email_verified = True
    user.is_active = True
    user.save(update_fields=["email_verified", "is_active"])
    return True, "verified", user


def resend_otp(email: str, purpose: str) -> str:
    """Re-issue a verification/reset code.

    Returns one of ``sent`` | ``no_account`` | ``already_verified`` so callers
    can respond without leaking which addresses have accounts.
    """
    user = get_user_by_email(email)
    if user is None:
        return "no_account"
    if purpose == OTPPurpose.EMAIL_VERIFICATION and user.email_verified:
        return "already_verified"
    issue_otp(user, purpose)
    return "sent"


# --- Login / sessions ---------------------------------------------------------


def authenticate_user(email: str, password: str) -> User | None:
    user = get_user_by_email(email)
    if user is None or not user.check_password(password):
        return None
    return user


def rotate_refresh_token(raw: str) -> tuple[User, dict] | None:
    """Validate the refresh, blacklist it (single-use), issue a fresh pair.

    Returns ``None`` when the token is invalid or already used.
    """
    try:
        refresh = RefreshToken(raw)
        user = get_user_by_id(refresh.payload.get("user_id"))
        if user is None:
            return None
        refresh.blacklist()
        return user, tokens_for(user)
    except TokenError:
        return None


def logout_user(raw: str | None) -> None:
    """Blacklist the refresh token best-effort so it can't be replayed."""
    if not raw:
        return
    try:
        RefreshToken(raw).blacklist()
    except TokenError:
        pass


# --- Google -------------------------------------------------------------------


def google_authenticate(email: str, id_token: str, name: str) -> User:
    """Find-or-create a Google account and sign it in.

    In real mode (``GOOGLE_CLIENT_ID`` set) the supplied request email is
    ignored — the verified token's payload is authoritative. Raises
    ``ValueError`` for any unrecoverable Google verification failure.
    """
    if settings.GOOGLE_CLIENT_ID:
        payload = _verify_google_id_token(id_token)
        email = (payload.get("email") or "").lower()
        if not email:
            raise ValueError("Could not read an email from the Google token.")
        sub = payload.get("sub")
        name = payload.get("name") or ""
    else:
        sub = None

    user = (
        get_user_by_google_sub(sub)
        if sub
        else get_user_by_email(email)
    )
    if user is None:
        user = User.objects.create_user(
            email=email,
            password=None,
            first_name=(name or "").strip(),
            email_verified=True,
            is_active=True,
            auth_provider="google",
            google_sub=sub,
        )
    else:
        _promote_to_google(user, sub)
    return user


# --- Password reset -----------------------------------------------------------


def forgot_password(email: str) -> None:
    """Issue a reset OTP when the address belongs to an account (silent otherwise)."""
    user = get_user_by_email(email)
    if user is not None:
        issue_otp(user, OTPPurpose.PASSWORD_RESET)


def reset_password(email: str, code: str, new_password: str) -> tuple[bool, str]:
    """Consume a reset code and replace the password.

    Returns ``(ok, reason)``; on success any other outstanding reset codes for
    the user are voided.
    """
    user = get_user_by_email(email)
    if user is None:
        return False, "invalid"

    otp = get_live_otp(user, OTPPurpose.PASSWORD_RESET)
    if otp is None:
        return False, "invalid"

    ok, reason = consume_otp(otp, code)
    if not ok:
        return False, reason

    user.set_password(new_password)
    user.email_verified = True
    user.is_active = True
    user.save(update_fields=["password", "email_verified", "is_active"])
    OTPCode.objects.filter(
        user=user,
        purpose=OTPPurpose.PASSWORD_RESET,
        used_at__isnull=True,
    ).update(used_at=timezone.now())
    return True, "verified"


# --- OTP ----------------------------------------------------------------------


def issue_otp(user: User, purpose: str) -> OTPCode:
    """Invalidate prior codes for this user+purpose, then issue a fresh one.

    Returns the persisted OTPCode row. The raw code is only logged / emailed —
    never persisted.
    """
    OTPCode.objects.filter(
        user=user, purpose=purpose, used_at__isnull=True
    ).update(used_at=timezone.now())

    code = generate_otp()
    otp = OTPCode.objects.create(
        user=user,
        purpose=purpose,
        code_hash=hash_code(code),
        expires_at=timezone.now()
        + timezone.timedelta(minutes=settings.AUTH_OTP_TTL_MINUTES),
    )
    _notify(otp, code)
    return otp


def consume_otp(otp: OTPCode, code: str) -> tuple[bool, str]:
    """Try to consume a code.

    Returns ``(ok, reason)`` where reason is one of
    ``verified|invalid|expired|used|too_many_attempts``.
    """
    if otp.used_at is not None:
        return False, "used"
    if otp.is_expired:
        return False, "expired"
    if otp.attempts >= getattr(settings, "AUTH_OTP_MAX_ATTEMPTS", 5):
        return False, "too_many_attempts"
    if not otp.matches(code):
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        return False, "invalid"
    otp.used_at = timezone.now()
    otp.save(update_fields=["used_at"])
    return True, "verified"


def tokens_for(user) -> dict[str, str]:
    """Issue a fresh access+refresh pair of JWT strings."""
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


# --- Internal helpers ---------------------------------------------------------


def _promote_to_google(user: User, sub) -> None:
    """Upgrade an existing (or re-returning) user to a verified Google account."""
    changed = []
    if not user.email_verified:
        user.email_verified = True
        changed.append("email_verified")
    if not user.is_active:
        user.is_active = True
        changed.append("is_active")
    if sub and not user.google_sub:
        user.google_sub = sub
        changed.append("google_sub")
    if changed:
        user.auth_provider = "google"
        changed.append("auth_provider")
        user.save(update_fields=changed)


def _verify_google_id_token(id_token: str) -> dict:
    """Return the Google tokeninfo payload for an id_token.

    Raises ``ValueError`` on any verification failure so the caller can turn
    it into a client error.
    """
    if not id_token:
        raise ValueError("A valid Google id_token is required.")
    url = (
        "https://oauth2.googleapis.com/tokeninfo?id_token="
        + urllib.request.quote(id_token)
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except (URLError, ValueError) as exc:
        logger.warning("Google tokeninfo request failed: %s", exc)
        raise ValueError("The Google token could not be verified.") from exc

    if payload.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise ValueError("The Google token was issued to a different client.")
    if not payload.get("email_verified"):
        raise ValueError("The Google account email is not verified.")
    return payload


def _notify(otp: OTPCode, code: str) -> None:
    try:
        send_mail(
            _subject(otp.purpose),
            _message(otp.purpose, code),
            settings.DEFAULT_FROM_EMAIL,
            [otp.user.email],
            fail_silently=True,
        )
    except Exception as exc:  # noqa: BLE001 — never break a signup on email failure
        logger.warning("OTP email delivery failed for %s: %s", otp.user.email, exc)


def _subject(purpose: str) -> str:
    if purpose == OTPPurpose.PASSWORD_RESET:
        return "TrazeIQ — password reset code"
    return "TrazeIQ — verify your email address"


def _message(purpose: str, code: str) -> str:
    action = (
        "reset your password"
        if purpose == OTPPurpose.PASSWORD_RESET
        else "verify your email address"
    )
    return (
        f"Your TrazeIQ verification code is:\n\n{code}\n\n"
        f"Enter it to {action}. It expires in "
        f"{settings.AUTH_OTP_TTL_MINUTES} minutes.\n\n"
        "If you didn't request this, you can safely ignore this email."
    )