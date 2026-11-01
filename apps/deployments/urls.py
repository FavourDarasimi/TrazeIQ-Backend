from django.urls import path

from . import views

app_name = "deployments"

urlpatterns = [
    path("", views.DeploymentRecordView.as_view(), name="record"),
    path(
        "projects/<uuid:project_id>/",
        views.ProjectDeploymentsView.as_view(),
        name="project-deployments",
    ),
    path(
        "projects/<uuid:project_id>/releases/",
        views.ProjectReleasesView.as_view(),
        name="project-releases",
    ),
    path(
        "webhooks/github/",
        views.GitHubWebhookView.as_view(),
        name="webhook-github",
    ),
    path(
        "webhooks/gitlab/",
        views.GitLabWebhookView.as_view(),
        name="webhook-gitlab",
    ),
]
