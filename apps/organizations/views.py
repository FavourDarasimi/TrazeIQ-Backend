from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from trazeiq_backend.responses import (
    ErrorCode,
    api_error,
    api_success,
    envelope_schema,
)

from .permissions import IsOrgOwnerOrAdmin
from .selectors import (
    get_organization_for_user,
    list_members,
    list_organizations_for_user,
)
from .serializers import (
    InviteInputSerializer,
    InviteOutputSerializer,
    MembershipAcceptOutputSerializer,
    MembershipOutputSerializer,
    OrganizationInputSerializer,
    OrganizationOutputSerializer,
)
from .services import accept_invite, create_invite, create_organization
from apps.auditlog.models import AuditAction
from apps.auditlog.services import record_audit_log
from .models import Membership, MembershipRole

ORG_NOT_FOUND = "This organization does not exist."


def _not_found():
    return api_error("NOT_FOUND", ORG_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)


class OrganizationListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["organizations"],
        operation_id="organizations_list",
        summary="List organizations",
        description="Organizations the caller is a member of, newest first.",
        responses={
            200: envelope_schema(
                "OrganizationListOk",
                payload=inline_serializer(
                    "OrganizationListData",
                    fields={
                        "organizations": OrganizationOutputSerializer(many=True),
                    },
                ),
            ),
            401: envelope_schema("OrganizationListUnauthorized", error=True),
        },
    )
    def get(self, request):
        organizations = list_organizations_for_user(request.user)
        return api_success(
            data={
                "organizations": OrganizationOutputSerializer(
                    organizations, many=True
                ).data
            }
        )

    @extend_schema(
        tags=["organizations"],
        operation_id="organizations_create",
        summary="Create an organization",
        description=(
            "Creates the organization and a Membership with role=owner for "
            "the caller."
        ),
        request=OrganizationInputSerializer,
        responses={
            201: envelope_schema(
                "OrganizationCreateOk",
                payload=inline_serializer(
                    "OrganizationCreateData",
                    fields={"organization": OrganizationOutputSerializer()},
                ),
            ),
            400: envelope_schema("OrganizationCreateValidation", error=True),
            401: envelope_schema("OrganizationCreateUnauthorized", error=True),
        },
    )
    def post(self, request):
        serializer = OrganizationInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = create_organization(
            name=serializer.validated_data["name"],
            owner=request.user,
        )
        return api_success(
            data={"organization": OrganizationOutputSerializer(organization).data},
            status=status.HTTP_201_CREATED,
        )


class OrganizationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["organizations"],
        operation_id="organizations_retrieve",
        summary="Organization detail",
        responses={
            200: envelope_schema(
                "OrganizationDetailOk",
                payload=inline_serializer(
                    "OrganizationDetailData",
                    fields={"organization": OrganizationOutputSerializer()},
                ),
            ),
            401: envelope_schema("OrganizationDetailUnauthorized", error=True),
            404: envelope_schema("OrganizationDetailNotFound", error=True),
        },
    )
    def get(self, request, pk):
        organization = get_organization_for_user(pk, request.user)
        if organization is None:
            raise NotFound(ORG_NOT_FOUND)
        return api_success(
            data={"organization": OrganizationOutputSerializer(organization).data}
        )


class OrganizationMembersView(APIView):
    """GET /api/organizations/{id}/members/ — the team roster.

    Read-only, so any member (including viewers) can see it; the tenant
    getter keeps foreign/unknown orgs at 404.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["organizations"],
        operation_id="organizations_members_list",
        summary="List organization members",
        description=(
            "Every membership of the organization, oldest first. Readable by "
            "any member; foreign or unknown organization ids stay 404."
        ),
        responses={
            200: envelope_schema(
                "OrganizationMembersOk",
                payload=inline_serializer(
                    "OrganizationMembersData",
                    fields={
                        "members": MembershipOutputSerializer(many=True),
                    },
                ),
            ),
            401: envelope_schema("OrganizationMembersUnauthorized", error=True),
            404: envelope_schema("OrganizationMembersNotFound", error=True),
        },
    )
    def get(self, request, pk):
        organization = get_organization_for_user(pk, request.user)
        if organization is None:
            raise NotFound(ORG_NOT_FOUND)
        members = list_members(organization)
        return api_success(
            data={
                "members": MembershipOutputSerializer(
                    members, many=True
                ).data
            }
        )


class OrganizationInviteView(APIView):
    """POST /api/organizations/{id}/invite/ — invite an address by email.

    Owner/admin only (enforced by the permission class); the raw invite token
    is returned exactly once, in this response.
    """

    permission_classes = [IsAuthenticated, IsOrgOwnerOrAdmin]

    @extend_schema(
        tags=["organizations"],
        operation_id="organizations_invite",
        summary="Invite a team member",
        description=(
            "Issue a pending invite for an email address with the given role "
            "(admin/developer/viewer — never owner). Returns the raw invite "
            "token exactly once; only its hash is stored. Re-inviting a "
            "pending address rotates the token."
        ),
        request=InviteInputSerializer,
        responses={
            201: envelope_schema(
                "OrganizationInviteOk",
                payload=inline_serializer(
                    "OrganizationInviteData",
                    fields={
                        "invite": InviteOutputSerializer(),
                        "invite_token": serializers.CharField(),
                    },
                ),
            ),
            400: envelope_schema("OrganizationInviteValidation", error=True),
            401: envelope_schema("OrganizationInviteUnauthorized", error=True),
            403: envelope_schema("OrganizationInviteForbidden", error=True),
            404: envelope_schema("OrganizationInviteNotFound", error=True),
            409: envelope_schema("OrganizationInviteConflict", error=True),
        },
    )
    def post(self, request, pk):
        organization = get_organization_for_user(pk, request.user)
        if organization is None:
            raise NotFound(ORG_NOT_FOUND)

        serializer = InviteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_invite(
            email=serializer.validated_data["email"],
            role=serializer.validated_data["role"],
            organization=organization,
            invited_by=request.user,
        )
        if result is None:
            return api_error(
                ErrorCode.ALREADY_MEMBER,
                "This address is already a member of the organization.",
                status=status.HTTP_409_CONFLICT,
            )
        invite, raw_token = result
        return api_success(
            data={
                "invite": InviteOutputSerializer(invite).data,
                "invite_token": raw_token,
            },
            status=status.HTTP_201_CREATED,
        )


class InviteAcceptView(APIView):
    """POST /api/invites/{token}/accept/ — claim an invite as the invitee.

    The caller must be signed in and their account's email must match the
    invite's email. Success creates a ``Membership`` with the invited role
    and consumes the token.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["organizations"],
        operation_id="invites_accept",
        summary="Accept a team invite",
        description=(
            "Consume the invite token and create the Membership with the "
            "invited role. The signed-in user's email must match the invite's "
            "addressed email."
        ),
        request=None,
        responses={
            201: envelope_schema(
                "InviteAcceptOk",
                payload=inline_serializer(
                    "InviteAcceptData",
                    fields={"membership": MembershipAcceptOutputSerializer()},
                ),
            ),
            400: envelope_schema("InviteAcceptInvalid", error=True),
            401: envelope_schema("InviteAcceptUnauthorized", error=True),
            403: envelope_schema("InviteAcceptMismatch", error=True),
            409: envelope_schema("InviteAcceptConflict", error=True),
        },
    )
    def post(self, request, token: str):
        reason, membership = accept_invite(token=token, user=request.user)

        if membership is not None:
            return api_success(
                data={
                    "membership": MembershipAcceptOutputSerializer(
                        {
                            "organization": membership.organization,
                            "role": membership.role,
                        }
                    ).data
                },
                status=status.HTTP_201_CREATED,
            )

        mapping = {
            "invalid": (ErrorCode.INVITE_INVALID, "This invite does not exist.", 400),
            "used": (ErrorCode.INVITE_USED, "This invite has already been used.", 400),
            "expired": (ErrorCode.INVITE_EXPIRED, "This invite has expired.", 400),
            "wrong_user": (
                ErrorCode.INVITE_EMAIL_MISMATCH,
                "This invite was sent to a different email address.",
                403,
            ),
            "already_member": (
                ErrorCode.ALREADY_MEMBER,
                "You are already a member of this organization.",
                409,
            ),
        }
        code, message, http_status = mapping[reason]
        return api_error(code, message, status=http_status)


class OrganizationMemberRemoveView(APIView):
    """DELETE /api/organizations/{id}/members/{user_id}/ — remove a member.

    Owner/admin only. Deletes the membership and writes an audit-log entry
    recording who removed whom.
    """

    permission_classes = [IsAuthenticated, IsOrgOwnerOrAdmin]

    @extend_schema(
        tags=["organizations"],
        operation_id="organizations_members_remove",
        summary="Remove a member",
        description=(
            "Delete a membership from the organization. Only owners/admins may "
            "do this; the action is recorded in the audit log."
        ),
        responses={
            200: envelope_schema("OrganizationMemberRemoveOk"),
            401: envelope_schema("OrganizationMemberRemoveUnauthorized", error=True),
            403: envelope_schema("OrganizationMemberRemoveForbidden", error=True),
            404: envelope_schema("OrganizationMemberRemoveNotFound", error=True),
        },
    )
    def delete(self, request, pk, user_id):
        organization = get_organization_for_user(pk, request.user)
        if organization is None:
            raise NotFound(ORG_NOT_FOUND)
        membership = Membership.objects.filter(
            organization_id=pk, user_id=user_id
        ).first()
        if membership is None:
            raise NotFound("This member does not belong to the organization.")
        removed_email = membership.user.email
        membership.delete()
        record_audit_log(
            actor=request.user,
            organization=organization,
            action=AuditAction.MEMBER_REMOVED,
            target=f"Removed member {removed_email}",
        )
        return api_success(data={}, message="Member removed.")


__all__ = [
    "InviteAcceptView",
    "OrganizationDetailView",
    "OrganizationInviteView",
    "OrganizationListView",
    "OrganizationMemberRemoveView",
    "OrganizationMembersView",
    "ORG_NOT_FOUND",
]
