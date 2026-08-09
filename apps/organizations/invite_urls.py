"""Invite-accept routes — mounted at ``/api/v1/invites/`` (the spec's
`/api/invites/{token}/accept/`), separate from the organizations prefix."""

from django.urls import path

from . import views

app_name = "invites"

urlpatterns = [
    path(
        "<token>/accept/",
        views.InviteAcceptView.as_view(),
        name="accept",
    ),
]
