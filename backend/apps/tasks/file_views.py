"""Endpoints of references and results (files), and their authorized download."""

import logging

from django.db.models import QuerySet
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import Role

from . import services, uploads
from .file_serializers import (
    AttachmentCreateSerializer,
    AttachmentSerializer,
    PayloadTooLarge,
    ReferenceCreateSerializer,
    ReferenceSerializer,
)
from .models import ReferenceKind, Task, TaskAttachment, TaskReference
from .permissions import HasBusinessRole, IsBuyer, IsCreative
from .views import CONFLICT, FORBIDDEN, INVALID, NOT_AUTHENTICATED, NOT_FOUND, Conflict

logger = logging.getLogger(__name__)

TOO_LARGE = OpenApiResponse(description="The file is bigger than MAX_UPLOAD_MB.")


class IgnoreClientContentNegotiation(BaseContentNegotiation):
    """A download answers with the file whatever the client's Accept header says."""

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class RejectOversizedUploadMixin:
    """Refuses an oversized upload before anything reads the body.

    DRF checks CSRF while authenticating, and Django's CSRF check parses the multipart body
    (it looks for a form token before the header). So the declared size has to be checked in
    `initial`, ahead of authentication, to avoid spooling a huge body to a temporary file for a
    request that is refused anyway. Nginx `client_max_body_size` stays the first line.
    (A separate authentication class would do the same but makes drf-spectacular emit two
    "cookieAuth" components.)
    """

    def initial(self, request: Request, *args, **kwargs):
        if request.method == "POST":
            try:
                uploads.check_declared_size(request.META.get("CONTENT_LENGTH"))
            except uploads.UploadTooLarge as error:
                raise PayloadTooLarge(str(error)) from error
        super().initial(request, *args, **kwargs)


def _visible_task(request: Request, task_id: int) -> Task:
    """The task if the user sees it; a foreign task is a 404, never a 403."""
    return get_object_or_404(Task.objects.visible_to(request.user), pk=task_id)


class TaskReferencesView(RejectOversizedUploadMixin, APIView):
    """References of a task: any viewer lists them, only the author buyer adds them."""

    permission_classes = [IsAuthenticated, HasBusinessRole]

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method == "POST":
            permissions += [IsBuyer()]
        return permissions

    @extend_schema(
        responses={
            200: ReferenceSerializer(many=True),
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
        }
    )
    def get(self, request: Request, task_id: int) -> Response:
        task = _visible_task(request, task_id)
        return Response(ReferenceSerializer(task.references.all(), many=True).data)

    @extend_schema(
        request={
            "application/json": ReferenceCreateSerializer,
            "multipart/form-data": ReferenceCreateSerializer,
        },
        responses={
            201: ReferenceSerializer,
            400: INVALID,
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: CONFLICT,
            413: TOO_LARGE,
        },
    )
    def post(self, request: Request, task_id: int) -> Response:
        """Adds a link (JSON) or a file (multipart) reference."""
        task = _visible_task(request, task_id)
        if task.buyer_id != request.user.pk:
            raise PermissionDenied("Добавлять референсы может только автор задачи.")
        body = ReferenceCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            reference = services.add_reference(
                task_id=task.pk, actor=request.user, **body.validated_data
            )
        except services.TaskConflictError as error:
            raise Conflict(str(error)) from error
        return Response(ReferenceSerializer(reference).data, status=201)


class TaskAttachmentsView(RejectOversizedUploadMixin, APIView):
    """Results of a task: any viewer lists them, only the assignee uploads them."""

    permission_classes = [IsAuthenticated, HasBusinessRole]

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method == "POST":
            permissions += [IsCreative()]
        return permissions

    @extend_schema(
        responses={
            200: AttachmentSerializer(many=True),
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
        }
    )
    def get(self, request: Request, task_id: int) -> Response:
        task = _visible_task(request, task_id)
        attachments = task.attachments.select_related("uploaded_by")
        return Response(AttachmentSerializer(attachments, many=True).data)

    @extend_schema(
        request={"multipart/form-data": AttachmentCreateSerializer},
        responses={
            201: AttachmentSerializer,
            400: INVALID,
            401: NOT_AUTHENTICATED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: CONFLICT,
            413: TOO_LARGE,
        },
    )
    def post(self, request: Request, task_id: int) -> Response:
        """Uploads a result file; the round and the author are set by the server."""
        task = _visible_task(request, task_id)
        if request.user.role != Role.CREATIVE or task.assignee_id != request.user.pk:
            raise PermissionDenied("Загружать результат может только исполнитель задачи.")
        body = AttachmentCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            attachment = services.add_attachment(
                task_id=task.pk, actor=request.user, **body.validated_data
            )
        except services.TaskConflictError as error:
            raise Conflict(str(error)) from error
        return Response(AttachmentSerializer(attachment).data, status=201)


class _DownloadView(APIView):
    """Streams a stored file as an attachment, for whoever sees the task it belongs to."""

    permission_classes = [IsAuthenticated, HasBusinessRole]
    content_negotiation_class = IgnoreClientContentNegotiation

    def get_queryset(self) -> QuerySet:
        raise NotImplementedError

    def get(self, request: Request, pk: int) -> FileResponse:
        item = get_object_or_404(self.get_queryset(), pk=pk)
        if not item.file:
            raise NotFound
        try:
            handle = item.file.open("rb")
        except FileNotFoundError as error:
            # The path is not logged or shown: only the id of the record.
            logger.error("File of %s #%s is missing in the storage", type(item).__name__, item.pk)
            raise NotFound from error
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=item.original_name,
            content_type=item.content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response


@extend_schema(
    responses={
        200: OpenApiTypes.BINARY,
        401: NOT_AUTHENTICATED,
        403: FORBIDDEN,
        404: NOT_FOUND,
    }
)
class AttachmentDownloadView(_DownloadView):
    """Downloads a result file (as an attachment, never inline)."""

    def get_queryset(self) -> QuerySet[TaskAttachment]:
        return TaskAttachment.objects.filter(task__in=Task.objects.visible_to(self.request.user))


@extend_schema(
    responses={
        200: OpenApiTypes.BINARY,
        401: NOT_AUTHENTICATED,
        403: FORBIDDEN,
        404: NOT_FOUND,
    }
)
class ReferenceDownloadView(_DownloadView):
    """Downloads a file reference; a link reference has nothing to download (404)."""

    def get_queryset(self) -> QuerySet[TaskReference]:
        return TaskReference.objects.filter(
            task__in=Task.objects.visible_to(self.request.user), kind=ReferenceKind.FILE
        )
