from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from uuid import UUID

from apps.organizations.selectors import (
    get_organization_for_user,
    list_organizations_for_user,
)

from apps.auditlog.models import AuditAction
from apps.auditlog.services import record_audit_log
from trazeiq_backend.responses import api_success, envelope_schema

from .permissions import IsProjectOwnerOrAdmin
from .selectors import get_project_for_user, list_projects_for_user
from .serializers import ProjectInputSerializer, ProjectOutputSerializer
from .services import (
    create_project,
    delete_project,
    integration_snippet,
    rotate_project_key,
    update_project,
)

PROJECT_NOT_FOUND = "This project does not exist."
ORGANIZATION_NOT_FOUND = "This organization does not exist."


def _project_schema(name: str):
    return inline_serializer(
        name,
        fields={"project": ProjectOutputSerializer()},
    )


class ProjectListView(APIView):
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # Creating a project is org management — owner/admin only. The org is
        # picked in the request body (or defaults to the caller's first org),
        # so the permission resolves it via get_permission_org_id below.
        if self.request.method == "POST":
            return [IsAuthenticated(), IsProjectOwnerOrAdmin()]
        return super().get_permissions()

    def get_permission_org_id(self, request):
        """The target org for the create permission: the body's organization,
        or the caller's first org — mirroring the default the create logic
        uses, so permission and view always agree."""
        raw = request.data.get("organization")
        if raw:
            try:
                return UUID(str(raw))
            except (TypeError, ValueError, AttributeError):
                return None
        organization = list_organizations_for_user(request.user).first()
        return organization.id if organization else None

    @extend_schema(
        tags=["projects"],
        operation_id="projects_list",
        summary="List projects",
        description=(
            "Projects in the organizations the caller is a member of. "
            "Only the API key prefix is exposed — the raw key exists in "
            "exactly one response, at creation time."
        ),
        responses={
            200: envelope_schema(
                "ProjectListOk",
                payload=inline_serializer(
                    "ProjectListData",
                    fields={"projects": ProjectOutputSerializer(many=True)},
                ),
            ),
            401: envelope_schema("ProjectListUnauthorized", error=True),
        },
    )
    def get(self, request):
        projects = list_projects_for_user(request.user)
        return api_success(
            data={
                "projects": ProjectOutputSerializer(projects, many=True).data
            }
        )

    @extend_schema(
        tags=["projects"],
        operation_id="projects_create",
        summary="Create a project",
        description=(
            "Generates a fresh API key, stores only its hash, and returns "
            "the raw key together with a copy-paste direct HTTP snippet — "
            "available exactly once, in this response."
        ),
        request=ProjectInputSerializer,
        responses={
            201: envelope_schema(
                "ProjectCreateOk",
                payload=inline_serializer(
                    "ProjectCreateData",
                    fields={
                        "project": ProjectOutputSerializer(),
                        "api_key": serializers.CharField(),
                        "integration_snippet": serializers.CharField(),
                    },
                ),
            ),
            400: envelope_schema("ProjectCreateValidation", error=True),
            401: envelope_schema("ProjectCreateUnauthorized", error=True),
            404: envelope_schema("ProjectCreateNotFound", error=True),
        },
    )
    def post(self, request):
        serializer = ProjectInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        organization_id = serializer.validated_data.get("organization")
        if organization_id is None:
            organization = list_organizations_for_user(request.user).first()
        else:
            organization = get_organization_for_user(
                organization_id, request.user
            )
        if organization is None:
            raise NotFound(ORGANIZATION_NOT_FOUND)

        project, raw_key = create_project(
            organization=organization,
            name=serializer.validated_data["name"],
            environment=serializer.validated_data["environment"],
        )
        return api_success(
            data={
                "project": ProjectOutputSerializer(project).data,
                "api_key": raw_key,
                "integration_snippet": integration_snippet(
                    raw_key, project.environment
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class ProjectDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # Reads are open to any member; writes are org management
        # (owner/admin only, enforced at the permission-class level).
        if self.request.method in ("PATCH", "DELETE"):
            return [IsAuthenticated(), IsProjectOwnerOrAdmin()]
        return super().get_permissions()

    @extend_schema(
        tags=["projects"],
        operation_id="projects_retrieve",
        summary="Project detail",
        responses={
            200: envelope_schema(
                "ProjectDetailOk",
                payload=inline_serializer(
                    "ProjectDetailData",
                    fields={"project": ProjectOutputSerializer()},
                ),
            ),
            401: envelope_schema("ProjectDetailUnauthorized", error=True),
            404: envelope_schema("ProjectDetailNotFound", error=True),
        },
    )
    def get(self, request, pk):
        project = get_project_for_user(pk, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)
        return api_success(data={"project": ProjectOutputSerializer(project).data})

    @extend_schema(
        tags=["projects"],
        operation_id="projects_update",
        summary="Update a project",
        description="Update the project's name and/or environment.",
        request=ProjectInputSerializer,
        responses={
            200: envelope_schema(
                "ProjectUpdateOk",
                payload=inline_serializer(
                    "ProjectUpdateData",
                    fields={"project": ProjectOutputSerializer()},
                ),
            ),
            400: envelope_schema("ProjectUpdateValidation", error=True),
            401: envelope_schema("ProjectUpdateUnauthorized", error=True),
            404: envelope_schema("ProjectUpdateNotFound", error=True),
        },
    )
    def patch(self, request, pk):
        project = get_project_for_user(pk, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)

        serializer = ProjectInputSerializer(
            data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        project = update_project(
            project,
            name=serializer.validated_data.get("name"),
            environment=serializer.validated_data.get("environment"),
        )
        return api_success(data={"project": ProjectOutputSerializer(project).data})

    @extend_schema(
        tags=["projects"],
        operation_id="projects_destroy",
        summary="Delete a project",
        responses={
            204: None,
            401: envelope_schema("ProjectDeleteUnauthorized", error=True),
            404: envelope_schema("ProjectDeleteNotFound", error=True),
        },
    )
    def delete(self, request, pk):
        project = get_project_for_user(pk, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)
        delete_project(project)
        return api_success(status=status.HTTP_204_NO_CONTENT)


class ProjectRotateKeyView(APIView):
    """POST /api/projects/{id}/rotate-key/ — regenerate the API key.

    Owner/admin only. The old key stops authenticating the moment the hash is
    replaced; the new raw key is returned exactly once.
    """

    permission_classes = [IsAuthenticated, IsProjectOwnerOrAdmin]

    @extend_schema(
        tags=["projects"],
        operation_id="projects_rotate_key",
        summary="Rotate the API key",
        description=(
            "Regenerates the project's API key: the stored hash is replaced "
            "and the previous key stops working immediately. The new raw key "
            "is returned exactly once, in this response."
        ),
        request=None,
        responses={
            200: envelope_schema(
                "ProjectRotateKeyOk",
                payload=inline_serializer(
                    "ProjectRotateKeyData",
                    fields={
                        "project": ProjectOutputSerializer(),
                        "api_key": serializers.CharField(),
                        "integration_snippet": serializers.CharField(),
                    },
                ),
            ),
            401: envelope_schema("ProjectRotateKeyUnauthorized", error=True),
            403: envelope_schema("ProjectRotateKeyForbidden", error=True),
            404: envelope_schema("ProjectRotateKeyNotFound", error=True),
        },
    )
    def post(self, request, pk):
        project = get_project_for_user(pk, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)

        project, raw_key = rotate_project_key(project)
        record_audit_log(
            actor=request.user,
            organization=project.organization,
            action=AuditAction.KEY_ROTATED,
            target=f"Rotated API key for project '{project.name}'",
        )
        return api_success(
            data={
                "project": ProjectOutputSerializer(project).data,
                "api_key": raw_key,
                "integration_snippet": integration_snippet(
                    raw_key, project.environment
                ),
            }
        )


__all__ = [
    "ProjectDetailView",
    "ProjectListView",
    "ProjectRotateKeyView",
    "ORGANIZATION_NOT_FOUND",
    "PROJECT_NOT_FOUND",
]
