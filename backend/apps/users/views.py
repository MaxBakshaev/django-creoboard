import logging

from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import CsrfProtectedAnonymousAuthentication
from .serializers import LoginSerializer, MeSerializer
from .throttling import LoginIPThrottle, LoginUsernameThrottle

logger = logging.getLogger(__name__)


class CsrfView(APIView):
    """Sets the `csrftoken` cookie; the SPA calls it once before the first unsafe request."""

    # Explicit exception to the IsAuthenticated default: needed before login.
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(responses={204: OpenApiResponse(description="The csrftoken cookie is set.")})
    def get(self, request: Request) -> Response:
        get_token(request)  # marks the cookie to be set by CsrfViewMiddleware
        return Response(status=status.HTTP_204_NO_CONTENT)


class LoginView(APIView):
    """Starts a session. CSRF is enforced, attempts are throttled per IP and per username."""

    # Explicit exception to the IsAuthenticated default: this is the login endpoint.
    permission_classes = [AllowAny]
    authentication_classes = [CsrfProtectedAnonymousAuthentication]
    throttle_classes = [LoginIPThrottle, LoginUsernameThrottle]

    @extend_schema(
        auth=[],  # public endpoint: no security scheme in the OpenAPI schema
        request=LoginSerializer,
        responses={
            200: MeSerializer,
            400: OpenApiResponse(description="Invalid data or wrong login or password."),
            403: OpenApiResponse(description="CSRF token is missing or invalid."),
            429: OpenApiResponse(description="Too many login attempts."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)  # rotates the session key and the CSRF token
        logger.info("User logged in: id=%s", user.pk)
        return Response(MeSerializer(user).data)


class LogoutView(APIView):
    """Ends the session. CSRF is enforced by the authentication class for logged-in users."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={
            204: OpenApiResponse(description="The session is closed."),
            401: OpenApiResponse(description="Not authenticated."),
        },
    )
    def post(self, request: Request) -> Response:
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """The current user."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: MeSerializer,
            401: OpenApiResponse(description="Not authenticated."),
        },
    )
    def get(self, request: Request) -> Response:
        return Response(MeSerializer(request.user).data)
