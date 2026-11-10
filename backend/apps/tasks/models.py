from django.conf import settings
from django.db import models

from apps.users.models import Role

MAX_QUANTITY = 500


class TaskStatus(models.TextChoices):
    NEW = "new", "Новые"
    IN_PROGRESS = "in_progress", "В работе"
    REVIEW = "review", "На проверке"
    REVISION = "revision", "Правки"
    DONE = "done", "Готово"


class TaskAction(models.TextChoices):
    """Actions a user can run on a task; the API exposes them as `allowed_actions`."""

    TAKE = "take", "Взять в работу"
    SUBMIT = "submit", "Сдать на проверку"
    ACCEPT = "accept", "Принять"
    REQUEST_REVISION = "request_revision", "Отправить на правки"


class CreativeType(models.TextChoices):
    STATIC = "static", "Статика"
    VIDEO = "video", "Видео"
    HTML5 = "html5", "HTML5"
    UGC = "ugc", "UGC"
    COPY = "copy", "Копирайтинг"
    OTHER = "other", "Другое"


class Priority(models.TextChoices):
    LOW = "low", "Низкий"
    NORMAL = "normal", "Обычный"
    HIGH = "high", "Высокий"
    URGENT = "urgent", "Срочный"


class TaskQuerySet(models.QuerySet):
    def visible_to(self, user) -> "TaskQuerySet":
        """Tasks the user may see (docs/ARCHITECTURE.md, section 5).

        Anyone else, including a user without a business role, sees nothing.
        """
        role = getattr(user, "role", "")
        if role == Role.LEAD:
            return self
        if role == Role.BUYER:
            return self.filter(buyer=user)
        if role == Role.CREATIVE:
            return self.filter(models.Q(status=TaskStatus.NEW) | models.Q(assignee=user))
        return self.none()


class Task(models.Model):
    """A creative brief. The status changes only through `apps.tasks.services`."""

    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_tasks",
        verbose_name="байер",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_tasks",
        null=True,
        blank=True,
        verbose_name="исполнитель",
    )
    offer = models.CharField("оффер", max_length=200)
    geo = models.CharField("гео", max_length=64)
    creative_type = models.CharField("тип креатива", max_length=16, choices=CreativeType.choices)
    quantity = models.PositiveIntegerField("количество")
    description = models.TextField("описание", max_length=5000)
    priority = models.CharField(
        "приоритет", max_length=16, choices=Priority.choices, default=Priority.NORMAL
    )
    deadline = models.DateTimeField("дедлайн")
    status = models.CharField(
        "статус", max_length=16, choices=TaskStatus.choices, default=TaskStatus.NEW
    )
    revision_count = models.PositiveSmallIntegerField("число правок", default=0)

    # Set by the server only (docs/ARCHITECTURE.md, section 6).
    created_at = models.DateTimeField(auto_now_add=True)
    taken_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status"], name="task_status_idx"),
            models.Index(fields=["assignee", "status"], name="task_assignee_status_idx"),
            models.Index(fields=["buyer"], name="task_buyer_idx"),
            models.Index(fields=["deadline"], name="task_deadline_idx"),
            models.Index(fields=["completed_at"], name="task_completed_at_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1, quantity__lte=MAX_QUANTITY),
                name="task_quantity_between_1_and_500",
            ),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.offer}"


class TaskEventAction(models.TextChoices):
    CREATED = "created", "Создана"
    UPDATED = "updated", "Изменена"
    TAKEN = "taken", "Взята в работу"
    SUBMITTED = "submitted", "Сдана на проверку"
    ACCEPTED = "accepted", "Принята"
    REVISION_REQUESTED = "revision_requested", "Отправлена на правки"
    REFERENCE_ADDED = "reference_added", "Добавлен референс"
    ATTACHMENT_ADDED = "attachment_added", "Добавлен результат"
    AI_USAGE_ADDED = "ai_usage_added", "Добавлен AI-расход"
    AI_USAGE_CHANGED = "ai_usage_changed", "Изменён AI-расход"
    AI_USAGE_DELETED = "ai_usage_deleted", "Удалён AI-расход"


class ImmutableRecordError(Exception):
    """Raised on any attempt to change or delete an append-only record."""


class TaskEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableRecordError("TaskEvent is append-only: update is not allowed.")

    def delete(self):
        raise ImmutableRecordError("TaskEvent is append-only: delete is not allowed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ImmutableRecordError("TaskEvent is append-only: update is not allowed.")

    def bulk_create(self, objs, *args, update_conflicts=False, **kwargs):
        if update_conflicts:
            raise ImmutableRecordError("TaskEvent is append-only: upsert is not allowed.")
        return super().bulk_create(objs, *args, update_conflicts=update_conflicts, **kwargs)


class TaskEvent(models.Model):
    """Append-only journal of what happened to a task."""

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="task_events",
    )
    action = models.CharField(max_length=32, choices=TaskEventAction.choices)
    from_status = models.CharField(max_length=16, choices=TaskStatus.choices, blank=True)
    to_status = models.CharField(max_length=16, choices=TaskStatus.choices, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TaskEventQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["task", "created_at"], name="taskevent_task_created_idx")]

    def __str__(self) -> str:
        return f"{self.action} #{self.task_id}"

    def save(self, *args, **kwargs):
        # A new object with an existing pk would make Django try an UPDATE first.
        if not self._state.adding or self.pk is not None:
            raise ImmutableRecordError("TaskEvent is append-only: update is not allowed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("TaskEvent is append-only: delete is not allowed.")


# --- files ----------------------------------------------------------------------------------

MAX_REFERENCES_PER_TASK = 20
MAX_ATTACHMENTS_PER_ROUND = 20
MAX_URL_LENGTH = 2000
MAX_NOTE_LENGTH = 500


class ReferenceKind(models.TextChoices):
    LINK = "link", "Ссылка"
    FILE = "file", "Файл"


class TaskReference(models.Model):
    """A reference the buyer attaches to a brief: a link (http/https) or an uploaded file.

    Files are stored under a random name; `original_name` is metadata for display only.
    """

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="references")
    kind = models.CharField(max_length=8, choices=ReferenceKind.choices)
    url = models.URLField(max_length=MAX_URL_LENGTH, blank=True)
    file = models.FileField(upload_to="references/", blank=True, max_length=255)
    original_name = models.CharField(max_length=255, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=MAX_NOTE_LENGTH, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["task"], name="taskref_task_idx")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="link", file="") & ~models.Q(url="")
                    | models.Q(kind="file", url="") & ~models.Q(file="")
                ),
                name="taskref_link_xor_file",
            ),
        ]

    def __str__(self) -> str:
        return f"reference #{self.pk} of task #{self.task_id}"


class TaskAttachment(models.Model):
    """A result file the assignee uploads. `round` is 0 for the first submission and N after
    the N-th revision request; the server sets it from `Task.revision_count`."""

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="attachments")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_attachments"
    )
    file = models.FileField(upload_to="attachments/", max_length=255)
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100)
    round = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["task", "round"], name="taskattach_task_round_idx")]

    def __str__(self) -> str:
        return f"attachment #{self.pk} of task #{self.task_id}"
