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
from apps.events.services import regroup_project_error_groups
from trazeiq_backend.requests import body_as_dict
from trazeiq_backend.responses import api_success, envelope_schema

from .permissions import (
    IsProjectDeveloper,
    IsProjectMember,
    IsProjectOwnerOrAdmin,
)
from .selectors import (
    get_project_api_key,
    get_project_for_user,
    list_project_api_keys,
    list_projects_for_user,
)
from .serializers import (
    ProjectApiKeyInputSerializer,
    ProjectApiKeyOutputSerializer,
    ProjectApiKeyRotateInputSerializer,
    ProjectInputSerializer,
    ProjectOutputSerializer,
)
from .services import (
    create_project,
    create_project_api_key,
    delete_project,
    integration_snippet,
    revoke_project_api_key,
    rotate_project_api_key,
    rotate_project_key,
    update_project,
)

PROJECT_NOT_FOUND = "This project does not exist."
ORGANIZATION_NOT_FOUND = "This organization does not exist."
KEY_NOT_FOUND = "This API key does not exist."


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
        raw = body_as_dict(request).get("organization")
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
            "available exactly once, in this response. The snippet targets "
            "the API host you called, so local evaluation posts to your "
            "dev server, not production."
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
                    raw_key,
                    project.environment,
                    base_url="%s://%s"
                    % (request.scheme, request.get_host()),
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

        grace_period_hours = 0
        if isinstance(request.data, dict) and "grace_period_hours" in request.data:
            try:
                grace_period_hours = int(request.data["grace_period_hours"])
            except (ValueError, TypeError):
                grace_period_hours = 0

        project, raw_key = rotate_project_key(
            project, grace_period_hours=grace_period_hours, user=request.user
        )
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
                    raw_key,
                    project.environment,
                    base_url="%s://%s"
                    % (request.scheme, request.get_host()),
                ),
            }
        )


class ProjectRegroupErrorsView(APIView):
    """POST /api/v1/projects/{id}/regroup/ — recompute fingerprints and consolidate groups."""

    permission_classes = [IsAuthenticated, IsProjectDeveloper]

    @extend_schema(
        tags=["projects"],
        operation_id="projects_regroup_errors",
        summary="Regroup project errors",
        description=(
            "Recompute fingerprint algorithm v2 for all events in the project. "
            "Collapses noisy split groups caused by wrappers, varying middleware, "
            "or changed line numbers into unified canonical ErrorGroups."
        ),
        request=inline_serializer(
            "ProjectRegroupInput",
            fields={
                "dry_run": serializers.BooleanField(
                    required=False, default=False
                )
            },
        ),
        responses={
            200: envelope_schema(
                "ProjectRegroupOk",
                payload=inline_serializer(
                    "ProjectRegroupData",
                    fields={
                        "dry_run": serializers.BooleanField(),
                        "project_id": serializers.UUIDField(),
                        "events_reprocessed": serializers.IntegerField(),
                        "groups_before": serializers.IntegerField(),
                        "groups_after": serializers.IntegerField(),
                        "groups_consolidated": serializers.IntegerField(),
                    },
                ),
            ),
            401: envelope_schema("ProjectRegroupUnauthorized", error=True),
            403: envelope_schema("ProjectRegroupForbidden", error=True),
            404: envelope_schema("ProjectRegroupNotFound", error=True),
        },
    )
    def post(self, request, pk):
        project = get_project_for_user(pk, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)
        dry_run = (
            bool(request.data.get("dry_run", False))
            if isinstance(request.data, dict)
            else False
        )
        result = regroup_project_error_groups(
            project, actor=request.user, dry_run=dry_run
        )
        return api_success(data=result)


class ProjectApiKeyListView(APIView):
    """GET/POST /api/v1/projects/{project_id}/keys/

    List active and historical API keys, or issue a new scoped API key.
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsProjectOwnerOrAdmin()]
        return [IsAuthenticated(), IsProjectMember()]

    @extend_schema(
        tags=["projects"],
        operation_id="project_api_keys_list",
        summary="List project API keys",
        responses={
            200: envelope_schema(
                "ProjectApiKeyListOk",
                payload=inline_serializer(
                    "ProjectApiKeyListData",
                    fields={"keys": ProjectApiKeyOutputSerializer(many=True)},
                ),
            ),
            401: envelope_schema("ProjectApiKeyListUnauthorized", error=True),
            404: envelope_schema("ProjectApiKeyListNotFound", error=True),
        },
    )
    def get(self, request, project_id):
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)

        keys = list_project_api_keys(project_id, request.user)
        return api_success(
            data={"keys": ProjectApiKeyOutputSerializer(keys, many=True).data}
        )

    @extend_schema(
        tags=["projects"],
        operation_id="project_api_keys_create",
        summary="Create a scoped project API key",
        request=ProjectApiKeyInputSerializer,
        responses={
            201: envelope_schema(
                "ProjectApiKeyCreateOk",
                payload=inline_serializer(
                    "ProjectApiKeyCreateData",
                    fields={
                        "key": ProjectApiKeyOutputSerializer(),
                        "api_key": serializers.CharField(),
                        "integration_snippet": serializers.CharField(),
                    },
                ),
            ),
            400: envelope_schema("ProjectApiKeyCreateValidation", error=True),
            401: envelope_schema("ProjectApiKeyCreateUnauthorized", error=True),
            403: envelope_schema("ProjectApiKeyCreateForbidden", error=True),
            404: envelope_schema("ProjectApiKeyCreateNotFound", error=True),
        },
    )
    def post(self, request, project_id):
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)

        serializer = ProjectApiKeyInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        key, raw_key = create_project_api_key(
            project,
            name=serializer.validated_data["name"],
            scopes=serializer.validated_data.get("scopes"),
            user=request.user,
        )

        record_audit_log(
            actor=request.user,
            organization=project.organization,
            action=AuditAction.KEY_CREATED,
            target=f"Created API key '{key.name}' ({key.api_key_prefix}...) for project '{project.name}'",
        )

        return api_success(
            data={
                "key": ProjectApiKeyOutputSerializer(key).data,
                "api_key": raw_key,
                "integration_snippet": integration_snippet(
                    raw_key,
                    project.environment,
                    base_url="%s://%s"
                    % (request.scheme, request.get_host()),
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class ProjectApiKeyDetailView(APIView):
    """DELETE /api/v1/projects/{project_id}/keys/{key_id}/ — revoke a key."""

    permission_classes = [IsAuthenticated, IsProjectOwnerOrAdmin]

    @extend_schema(
        tags=["projects"],
        operation_id="project_api_keys_revoke",
        summary="Revoke a project API key",
        responses={
            200: envelope_schema(
                "ProjectApiKeyRevokeOk",
                payload=inline_serializer(
                    "ProjectApiKeyRevokeData",
                    fields={"key": ProjectApiKeyOutputSerializer()},
                ),
            ),
            401: envelope_schema("ProjectApiKeyRevokeUnauthorized", error=True),
            403: envelope_schema("ProjectApiKeyRevokeForbidden", error=True),
            404: envelope_schema("ProjectApiKeyRevokeNotFound", error=True),
        },
    )
    def delete(self, request, project_id, key_id):
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)

        key = get_project_api_key(project_id, key_id, request.user)
        if key is None:
            raise NotFound(KEY_NOT_FOUND)

        revoked = revoke_project_api_key(key)
        record_audit_log(
            actor=request.user,
            organization=project.organization,
            action=AuditAction.KEY_REVOKED,
            target=f"Revoked API key '{key.name}' ({key.api_key_prefix}...) for project '{project.name}'",
        )
        return api_success(
            data={"key": ProjectApiKeyOutputSerializer(revoked).data}
        )


class ProjectApiKeyRotateView(APIView):
    """POST /api/v1/projects/{project_id}/keys/{key_id}/rotate/

    Rotate a specific key with an optional grace period window.
    """

    permission_classes = [IsAuthenticated, IsProjectOwnerOrAdmin]

    @extend_schema(
        tags=["projects"],
        operation_id="project_api_keys_rotate",
        summary="Rotate a specific project API key",
        request=ProjectApiKeyRotateInputSerializer,
        responses={
            200: envelope_schema(
                "ProjectApiKeyRotateOk",
                payload=inline_serializer(
                    "ProjectApiKeyRotateData",
                    fields={
                        "key": ProjectApiKeyOutputSerializer(),
                        "old_key": ProjectApiKeyOutputSerializer(),
                        "api_key": serializers.CharField(),
                        "integration_snippet": serializers.CharField(),
                    },
                ),
            ),
            400: envelope_schema("ProjectApiKeyRotateValidation", error=True),
            401: envelope_schema("ProjectApiKeyRotateUnauthorized", error=True),
            403: envelope_schema("ProjectApiKeyRotateForbidden", error=True),
            404: envelope_schema("ProjectApiKeyRotateNotFound", error=True),
        },
    )
    def post(self, request, project_id, key_id):
        project = get_project_for_user(project_id, request.user)
        if project is None:
            raise NotFound(PROJECT_NOT_FOUND)

        key = get_project_api_key(project_id, key_id, request.user)
        if key is None:
            raise NotFound(KEY_NOT_FOUND)

        serializer = ProjectApiKeyRotateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        grace_period_hours = serializer.validated_data.get("grace_period_hours", 24)
        new_name = serializer.validated_data.get("name") or key.name

        new_key, raw_key, old_key = rotate_project_api_key(
            key,
            grace_period_hours=grace_period_hours,
            name=new_name,
            user=request.user,
        )

        record_audit_log(
            actor=request.user,
            organization=project.organization,
            action=AuditAction.KEY_ROTATED,
            target=(
                f"Rotated key '{old_key.name}' ({old_key.api_key_prefix}...) -> "
                f"'{new_key.name}' ({new_key.api_key_prefix}...) with {grace_period_hours}h grace period"
            ),
        )

        return api_success(
            data={
                "key": ProjectApiKeyOutputSerializer(new_key).data,
                "old_key": ProjectApiKeyOutputSerializer(old_key).data,
                "api_key": raw_key,
                "integration_snippet": integration_snippet(
                    raw_key,
                    project.environment,
                    base_url="%s://%s"
                    % (request.scheme, request.get_host()),
                ),
            }
        )


__all__ = [
    "ProjectDetailView",
    "ProjectListView",
    "ProjectRotateKeyView",
    "ProjectRegroupErrorsView",
    "ProjectApiKeyListView",
    "ProjectApiKeyDetailView",
    "ProjectApiKeyRotateView",
    "ORGANIZATION_NOT_FOUND",
    "PROJECT_NOT_FOUND",
    "KEY_NOT_FOUND",
]
