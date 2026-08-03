"""Health check endpoint for monitoring TrazeIQ itself."""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@extend_schema(
    tags=["system"],
    summary="Health check",
    description="Liveness probe — returns 200 whenever the API is running.",
    responses={
        200: inline_serializer(
            "HealthStatus",
            fields={"status": serializers.CharField()},
        )
    },
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"}, status=status.HTTP_200_OK)