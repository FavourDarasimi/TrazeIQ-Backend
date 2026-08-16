"""Read-side queries for the notification inbox — always scoped to the
caller's own rows; a user can only ever see their own notifications."""

from uuid import UUID

from django.db.models import QuerySet

from .models import AlertPreference, Notification

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def list_notifications_for_user(
    user, *, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> QuerySet[Notification]:
    """The caller's inbox, newest first."""
    limit = min(max(limit, 1), MAX_LIMIT)
    return (
        Notification.objects.filter(recipient=user)
        .select_related("incident__error_group")
        .order_by("-created_at", "-id")[offset : offset + limit]
    )


def unread_count_for_user(user) -> int:
    return Notification.objects.filter(recipient=user, is_read=False).count()


def get_alert_preferences(user) -> AlertPreference:
    """The caller's preferences; the default row is created on first read so
    the endpoints can always return the four knobs without null handling."""
    preferences, _ = AlertPreference.objects.get_or_create(user=user)
    return preferences


def get_notification_for_user(notification_id: UUID, user) -> Notification | None:
    """One notification the user can access, or ``None`` — unknown and
    foreign ids resolve to ``None`` and surface as 404."""
    return (
        Notification.objects.filter(id=notification_id, recipient=user).first()
    )