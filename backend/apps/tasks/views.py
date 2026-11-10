from django.db.models import QuerySet
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from . import services
from .filters import TaskFilter
from .models import Task
from .permissions import CanEditTask, HasBusinessRole, IsBuyer, IsCreative
from .serializers import RevisionRequestSerializer, TaskEventSerializer, TaskSerializer

NOT_AUTHENTICATED = OpenApiResponse(description="Not authenticated.")
FORBIDDEN = OpenApiResponse(description="The role or the task state does not allow it.")
NOT_FOUND = OpenApiResponse(description="No such task (or it is not visible to the user).")
INVALID = OpenApiResponse(description="Invalid data.")
CONFLICT = OpenApiResponse(description="The task is in a status that does not allow the action.")


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Конфликт состояния задачи."
    default_code = "conflict"


@extend_schema_view(
    list=extend_schema(responses={200: TaskSerializer(many=True), 401: NOT_AUTHENTICATED}),
    retrieve=extend_schema(
        responses={200: TaskSerializer, 401: NOT_AUTHENTICATED, 403: FORBIDDEN, 404: NOT_FOUND},
    ),
    create=extend_schema(
        request=TaskSerializer,
        responses={201: TaskSerializer, 400: INVALID, 401: NOT_AUTHENTICATED, 403: FORBIDDEN},
    ),
    partial_update=extend_schema(
        request=TaskSerializer,
        responses={
            200: TaskSerializer,
            400: INVALID,
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: OpenApiResponse(description='The task has left the "new" status meanwhile.'),
        },
    ),
)
class TaskViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Tasks. The queryset is limited by role, so a foreign task answers 404 (not 403)."""

    serializer_class = TaskSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = TaskFilter
    permission_classes = [IsAuthenticated, HasBusinessRole]
    # No PUT and no DELETE: a task is edited partially and never removed through the API.
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self) -> QuerySet[Task]:
        return Task.objects.visible_to(self.request.user).select_related("buyer", "assignee")

    def filter_queryset(self, queryset: QuerySet[Task]) -> QuerySet[Task]:
        # List filters must not turn a visible task into a 404 on the detail and events URLs.
        if self.action == "list":
            return super().filter_queryset(queryset)
        return queryset

    def get_permissions(self) -> list[BasePermission]:
        permissions = super().get_permissions()
        if self.action == "create":
            permissions.append(IsBuyer())
        elif self.action == "partial_update":
            permissions.append(CanEditTask())
        elif self.action in {"take", "submit"}:
            permissions.append(IsCreative())
        elif self.action in {"accept", "request_revision"}:
            permissions.append(IsBuyer())
        return permissions

    def perform_create(self, serializer: TaskSerializer) -> None:
        serializer.instance = services.create_task(
            buyer=self.request.user, data=serializer.validated_data
        )

    def perform_update(self, serializer: TaskSerializer) -> None:
        try:
            serializer.instance = services.update_task(
                task_id=serializer.instance.pk,
                actor=self.request.user,
                data=serializer.validated_data,
            )
        except services.TaskConflictError as error:
            raise Conflict(str(error)) from error

    @extend_schema(
        request=None,
        responses={
            200: TaskSerializer,
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: OpenApiResponse(description="The task cannot be taken (no details are given)."),
        },
    )
    @action(detail=True, methods=["post"], url_path="take")
    def take(self, request: Request, pk: str | None = None) -> Response:
        """A creative takes a "new" task.

        Deliberate exception to "foreign tasks are 404": a task that exists but is no longer
        "new" answers 409 with a neutral text, so that a stale board gets a useful answer and
        nothing about the task or its assignee is revealed. Only creatives get here.
        """
        if pk is None or not (pk.isascii() and pk.isdecimal()):
            raise NotFound
        try:
            task = services.take(task_id=int(pk), actor=request.user)
        except Task.DoesNotExist as error:
            raise NotFound from error
        except services.TaskConflictError as error:
            raise Conflict(str(error)) from error
        return Response(self.get_serializer(task).data)

    @extend_schema(
        request=None,
        responses={
            200: TaskSerializer,
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: CONFLICT,
        },
    )
    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request: Request, pk: str | None = None) -> Response:
        """The assignee sends the work to review."""
        return self._transition(services.submit)

    @extend_schema(
        request=None,
        responses={
            200: TaskSerializer,
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: CONFLICT,
        },
    )
    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request: Request, pk: str | None = None) -> Response:
        """The buyer who created the task accepts the result."""
        return self._transition(services.accept)

    @extend_schema(
        request=RevisionRequestSerializer,
        responses={
            200: TaskSerializer,
            400: INVALID,
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: CONFLICT,
        },
    )
    @action(detail=True, methods=["post"], url_path="request-revision")
    def request_revision(self, request: Request, pk: str | None = None) -> Response:
        """The buyer who created the task sends it back for changes; a comment is required."""
        body = RevisionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self._transition(services.request_revision, comment=body.validated_data["comment"])

    def _transition(self, function, **extra) -> Response:
        task = self.get_object()  # a task the user cannot see is a 404
        try:
            result = function(task_id=task.pk, actor=self.request.user, **extra)
        except services.TaskConflictError as error:
            raise Conflict(str(error)) from error
        return Response(self.get_serializer(result).data)

    @extend_schema(
        responses={200: TaskEventSerializer(many=True), 401: NOT_AUTHENTICATED, 404: NOT_FOUND},
    )
    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request: Request, pk: str | None = None) -> Response:
        """History of the task, oldest first, not paginated. Visible to whoever sees the task."""
        task = self.get_object()
        events = task.events.select_related("actor")
        return Response(TaskEventSerializer(events, many=True).data)
