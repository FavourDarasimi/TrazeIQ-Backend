from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from trazeiq_backend.responses import api_error, api_success, envelope_schema

from .selectors import get_organization_for_user, list_organizations_for_user
from .serializers import OrganizationInSerializer, OrganizationOutSerializer
from .services import create_organization

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
                        "organizations": OrganizationOutSerializer(many=True),
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
                "organizations": OrganizationOutSerializer(
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
        request=OrganizationInSerializer,
        responses={
            201: envelope_schema(
                "OrganizationCreateOk",
                payload=inline_serializer(
                    "OrganizationCreateData",
                    fields={"organization": OrganizationOutSerializer()},
                ),
            ),
            400: envelope_schema("OrganizationCreateValidation", error=True),
            401: envelope_schema("OrganizationCreateUnauthorized", error=True),
        },
    )
    def post(self, request):
        serializer = OrganizationInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = create_organization(
            name=serializer.validated_data["name"],
            owner=request.user,
        )
        return api_success(
            data={"organization": OrganizationOutSerializer(organization).data},
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
                    fields={"organization": OrganizationOutSerializer()},
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
            data={"organization": OrganizationOutSerializer(organization).data}
        )


__all__ = [
    "OrganizationDetailView",
    "OrganizationListView",
    "ORG_NOT_FOUND",
]
