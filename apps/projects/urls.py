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
]