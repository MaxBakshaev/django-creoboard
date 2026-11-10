from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.users.models import Role

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
