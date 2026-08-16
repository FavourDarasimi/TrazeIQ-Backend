from django.urls import path

from .views import (
    AlertPreferenceView,
    NotificationListView,
    NotificationMarkReadView,
    NotificationUnreadCountView,
)

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification-list"),
    path(
        "unread-count/",
        NotificationUnreadCountView.as_view(),
        name="notification-unread-count",
    ),
    path(
        "read/",
        NotificationMarkReadView.as_view(),
        name="notification-mark-read",
    ),
    path(
        "preferences/",
        AlertPreferenceView.as_view(),
        name="alert-preferences",
    ),
]