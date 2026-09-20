from .models import OTPCode, OTPPurpose, User


def get_user_by_email(email: str) -> User | None:
    return User.objects.filter(email=email).first()


def get_user_by_username(username: str) -> User | None:
    """Fetch by public handle (callers lowercase first; stored lowercase)."""
    return User.objects.filter(username=username).first()


def get_user_by_id(user_id) -> User | None:
    try:
        return User.objects.get(id=user_id) if user_id is not None else None
    except User.DoesNotExist:
        return None


def get_user_by_google_sub(sub: str) -> User | None:
    return User.objects.filter(google_sub=sub).first()


def user_exists(email: str) -> bool:
    return User.objects.filter(email=email).exists()


def username_exists(username: str) -> bool:
    """Case-insensitive: the DB unique constraint is case-sensitive, so the
    application layer owns the case-insensitive guarantee."""
    return User.objects.filter(username__iexact=username).exists()


def get_live_otp(user: User, purpose: OTPPurpose) -> OTPCode | None:
    qs = OTPCode.objects.filter(
        user=user, purpose=purpose, used_at__isnull=True
    ).order_by("-created_at")
    for otp in qs:
        if not otp.is_expired:
            return otp
    return None


def get_live_email_otp(email: str, purpose: OTPPurpose) -> OTPCode | None:
    """The freshest live code for a not-yet-registered address."""
    qs = OTPCode.objects.filter(
        email=email, purpose=purpose, used_at__isnull=True
    ).order_by("-created_at")
    for otp in qs:
        if not otp.is_expired:
            return otp
    return None