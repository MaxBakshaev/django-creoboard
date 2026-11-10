from django.db.models import QuerySet
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from . import services
from .filters import TaskFilter
from .models import Task
from .permissions import CanEditTask, HasBusinessRole, IsBuyer
from .serializers import TaskEventSerializer, TaskSerializer

NOT_AUTHENTICATED = OpenApiResponse(description="Not authenticated.")
FORBIDDEN = OpenApiResponse(description="The role or the task state does not allow it.")
NOT_FOUND = OpenApiResponse(description="No such task (or it is not visible to the user).")
INVALID = OpenApiResponse(description="Invalid data.")


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
        responses={200: TaskEventSerializer(many=True), 401: NOT_AUTHENTICATED, 404: NOT_FOUND},
    )
    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request: Request, pk: str | None = None) -> Response:
        """History of the task, oldest first, not paginated. Visible to whoever sees the task."""
        task = self.get_object()
        events = task.events.select_related("actor")
        return Response(TaskEventSerializer(events, many=True).data)
