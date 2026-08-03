from .models import OTPCode, OTPPurpose, User


def get_user_by_email(email: str) -> User | None:
    return User.objects.filter(email=email).first()


def get_user_by_id(user_id) -> User | None:
    try:
        return User.objects.get(id=user_id) if user_id is not None else None
    except User.DoesNotExist:
        return None


def get_user_by_google_sub(sub: str) -> User | None:
    return User.objects.filter(google_sub=sub).first()


def user_exists(email: str) -> bool:
    return User.objects.filter(email=email).exists()


def get_live_otp(user: User, purpose: OTPPurpose) -> OTPCode | None:
    qs = OTPCode.objects.filter(
        user=user, purpose=purpose, used_at__isnull=True
    ).order_by("-created_at")
    for otp in qs:
        if not otp.is_expired:
            return otp
    return None