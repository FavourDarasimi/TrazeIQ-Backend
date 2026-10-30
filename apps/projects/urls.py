from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="list"),
    path("<uuid:pk>/", views.ProjectDetailView.as_view(), name="detail"),
    path(
        "<uuid:pk>/rotate-key/",
        views.ProjectRotateKeyView.as_view(),
        name="rotate-key",
    ),
    path(
        "<uuid:project_id>/keys/",
        views.ProjectApiKeyListView.as_view(),
        name="keys-list",
    ),
    path(
        "<uuid:project_id>/keys/<uuid:key_id>/",
        views.ProjectApiKeyDetailView.as_view(),
        name="keys-detail",
    ),
    path(
        "<uuid:project_id>/keys/<uuid:key_id>/rotate/",
        views.ProjectApiKeyRotateView.as_view(),
        name="keys-rotate",
    ),
]
