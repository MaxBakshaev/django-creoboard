"""Project-level API views that do not belong to a domain app."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness probe for Docker and CI. Returns no data and never touches the DB."""

    # Explicit exception to the IsAuthenticated default: health must be reachable
    # by container healthchecks.
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        responses=OpenApiResponse(
            response=inline_serializer("Health", {"status": serializers.CharField()}),
            description="The service is alive.",
        ),
    )
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})
