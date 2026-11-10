from django.urls import path
from rest_framework.routers import SimpleRouter

from .file_views import (
    AttachmentDownloadView,
    ReferenceDownloadView,
    TaskAttachmentsView,
    TaskReferencesView,
)
from .views import TaskViewSet

router = SimpleRouter()
router.register("tasks", TaskViewSet, basename="task")

urlpatterns = [
    path("tasks/<int:task_id>/references/", TaskReferencesView.as_view(), name="task-references"),
    path(
        "tasks/<int:task_id>/attachments/",
        TaskAttachmentsView.as_view(),
        name="task-attachments",
    ),
    path(
        "attachments/<int:pk>/download/",
        AttachmentDownloadView.as_view(),
        name="attachment-download",
    ),
    path(
        "references/<int:pk>/download/",
        ReferenceDownloadView.as_view(),
        name="reference-download",
    ),
    *router.urls,
]
