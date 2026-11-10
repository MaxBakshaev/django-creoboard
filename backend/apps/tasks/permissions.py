from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.users.models import Role

from . import uploads
from .file_serializers import PayloadTooLarge
from .models import Task, TaskStatus


class HasBusinessRole(BasePermission):
    """Buyer, creative or lead. A user without a role (the superuser) gets no API access."""

    message = "Для этого действия нужна роль в сервисе."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return getattr(request.user, "role", "") in Role.values


class IsBuyer(BasePermission):
    message = "Действие доступно только байеру."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return getattr(request.user, "role", "") == Role.BUYER


class IsCreative(BasePermission):
    message = "Действие доступно только креативщику."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return getattr(request.user, "role", "") == Role.CREATIVE


class CanEditTask(BasePermission):
    """The buyer who created the task may edit it while it is still "new".

    Object-level only: a task the user cannot see never gets here (the queryset answers 404).
    """

    message = "Править задачу может её автор, пока она в статусе «Новые»."

    def has_object_permission(self, request: Request, view: APIView, obj: Task) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return (
            getattr(request.user, "role", "") == Role.BUYER
            and obj.buyer_id == request.user.pk
            and obj.status == TaskStatus.NEW
        )


class UploadSizeLimit(BasePermission):
    """Refuses a request whose declared body is too big before the body is read.

    The file limit is checked again after parsing (`uploads.check_upload`); this only avoids
    spooling a huge body to disk. A request without Content-Length (chunked) passes here.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        declared = request.META.get("CONTENT_LENGTH") or ""
        too_big = declared.isascii() and declared.isdecimal()
        if too_big and int(declared) > uploads.max_request_bytes():
            raise PayloadTooLarge
        return True
