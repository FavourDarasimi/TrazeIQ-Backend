from uuid import UUID
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from apps.events.authentication import APIKeyAuthentication
from apps.projects.models import ApiKeyScope, Project, ProjectApiKey
from apps.projects.selectors import get_project_for_user
from apps.projects.utils import hash_api_key
from trazeiq_backend.responses import api_error, api_success

from .models import Deployment
from .selectors import get_project_deployments, get_project_releases
from .serializers import (
    DeploymentInputSerializer,
    DeploymentOutputSerializer,
    ReleaseOutputSerializer,
)
from .services import (
    process_github_webhook,
    process_gitlab_webhook,
    record_deployment,
)


def _resolve_project_from_key(raw_key: str) -> Project | None:
    """Resolve a Project from a raw API key (either scoped ProjectApiKey or legacy project key)."""
    if not raw_key:
        return None
    digest = hash_api_key(raw_key.strip())
    api_key = (
        ProjectApiKey.objects.filter(api_key_hash=digest)
        .select_related("project")
        .first()
    )
    if api_key and api_key.is_valid:
        return api_key.project
    return Project.objects.filter(api_key_hash=digest).first()


class DeploymentRecordView(APIView):
    """POST /api/v1/deployments/ — Record a deployment marker.

    Can be called:
    1. By CI/CD scripts & SDKs using ``X-API-Key`` header (ingest_only or full_access).
    2. By logged-in developers passing ``project_id`` in the body.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        raw_key = request.headers.get("X-API-Key")
        project = None

        if raw_key:
            project = _resolve_project_from_key(raw_key)
            if project is None:
                raise NotAuthenticated("Invalid API key.")
        elif request.user and request.user.is_authenticated and hasattr(request.user, "email"):
            # Authenticated via user JWT/session
            pass
        else:
            raise NotAuthenticated("Authentication required: provide X-API-Key header or user token.")

        serializer = DeploymentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if project is None:
            project_id = data.get("project_id")
            if not project_id:
                raise ValidationError({"project_id": "project_id is required when authenticating with a user session."})
            project = get_project_for_user(project_id, request.user)
            if project is None:
                raise NotFound("Project not found or you lack access to it.")

        deployment = record_deployment(
            project=project,
            release_version=data["release"],
            environment=data.get("environment", "production"),
            status=data.get("status", Deployment.Status.COMPLETED),
            commit_sha=data.get("commit_sha", ""),
            commit_message=data.get("commit_message", ""),
            commit_author=data.get("commit_author", ""),
            commit_url=data.get("commit_url", ""),
            pull_request_url=data.get("pull_request_url", ""),
            pull_request_title=data.get("pull_request_title", ""),
            deployed_at=data.get("deployed_at"),
            metadata=data.get("metadata", {}),
            auto_resolve_incidents=data.get("auto_resolve", False),
            verified_incidents=data.get("verified_incidents", []),
        )

        return api_success(
            data={"deployment": DeploymentOutputSerializer(deployment).data},
            status=status.HTTP_201_CREATED,
        )


class ProjectDeploymentsView(APIView):
    """GET /api/v1/projects/{project_id}/deployments/ — List deployments for a project."""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id: UUID):
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound("Project not found.")

        env = request.query_params.get("environment")
        deployments = get_project_deployments(project.pk, environment=env)
        return api_success(
            data={"deployments": DeploymentOutputSerializer(deployments, many=True).data}
        )


class ProjectReleasesView(APIView):
    """GET /api/v1/projects/{project_id}/releases/ — List releases for a project."""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id: UUID):
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound("Project not found.")

        releases = get_project_releases(project.pk)
        return api_success(
            data={"releases": ReleaseOutputSerializer(releases, many=True).data}
        )


class GitHubWebhookView(APIView):
    """POST /api/v1/deployments/webhooks/github/ — GitHub deployment/release webhook receiver."""

    permission_classes = [AllowAny]

    def post(self, request):
        raw_key = (
            request.query_params.get("key")
            or request.headers.get("X-API-Key")
            or request.headers.get("X-TrazeIQ-Key")
        )
        project = _resolve_project_from_key(raw_key)
        if project is None:
            raise NotAuthenticated("Missing or invalid project key.")

        event_type = request.headers.get("X-GitHub-Event", "deployment_status")
        deployment = process_github_webhook(project, request.data, event_type=event_type)

        return api_success(
            data={
                "ok": True,
                "recorded": deployment is not None,
                "deployment_id": str(deployment.pk) if deployment else None,
            }
        )


class GitLabWebhookView(APIView):
    """POST /api/v1/deployments/webhooks/gitlab/ — GitLab deployment webhook receiver."""

    permission_classes = [AllowAny]

    def post(self, request):
        raw_key = (
            request.query_params.get("key")
            or request.headers.get("X-Gitlab-Token")
            or request.headers.get("X-API-Key")
        )
        project = _resolve_project_from_key(raw_key)
        if project is None:
            raise NotAuthenticated("Missing or invalid project key.")

        deployment = process_gitlab_webhook(project, request.data)

        return api_success(
            data={
                "ok": True,
                "recorded": deployment is not None,
                "deployment_id": str(deployment.pk) if deployment else None,
            }
        )
