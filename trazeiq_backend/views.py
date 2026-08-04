"""Health check endpoint for monitoring TrazeIQ itself."""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from trazeiq_backend.responses import api_success, envelope_schema


@extend_schema(
    tags=["system"],
    summary="Health check",
    description="Liveness probe — returns the envelope with 200 whenever the API is up.",
    responses={
        200: envelope_schema(
            "HealthOk",
            payload=inline_serializer(
                "HealthData",
                fields={"status": serializers.CharField()},
            ),
        )
    },
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return api_success({"status": "ok"}, status=status.HTTP_200_OK)