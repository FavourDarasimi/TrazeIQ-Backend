from django.urls import path

from .views import SlackConnectView, SlackStatusView

urlpatterns = [
    path("slack/connect/", SlackConnectView.as_view(), name="slack-connect"),
    path("slack/status/", SlackStatusView.as_view(), name="slack-status"),
]