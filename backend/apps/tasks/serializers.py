from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.users.serializers import UserBriefSerializer

from . import services
from .models import MAX_QUANTITY, Task, TaskAction, TaskEvent


class TaskSerializer(serializers.ModelSerializer):
    """The brief is writable; status, people, counters and timestamps are server-controlled."""

    buyer = UserBriefSerializer(read_only=True)
    assignee = UserBriefSerializer(read_only=True)
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "buyer",
            "assignee",
            "offer",
            "geo",
            "creative_type",
            "quantity",
            "description",
            "priority",
            "deadline",
            "status",
            "revision_count",
            "created_at",
            "taken_at",
            "submitted_at",
            "completed_at",
            "updated_at",
            "allowed_actions",
        ]
        read_only_fields = [
            "id",
            "status",
            "revision_count",
            "created_at",
            "taken_at",
            "submitted_at",
            "completed_at",
            "updated_at",
        ]
        extra_kwargs = {"quantity": {"min_value": 1, "max_value": MAX_QUANTITY}}

    @extend_schema_field(serializers.ListField(child=serializers.ChoiceField(TaskAction.choices)))
    def get_allowed_actions(self, task: Task) -> list[str]:
        """What the current user can do with the task; the backend is the only source."""
        request = self.context.get("request")
        return services.allowed_actions(task, request.user) if request is not None else []

    def validate_deadline(self, value):
        # An unchanged deadline stays valid even after it has passed, so that a buyer can fix
        # a typo in another field of an overdue task.
        unchanged = self.instance is not None and value == self.instance.deadline
        if not unchanged and value <= timezone.now():
            raise serializers.ValidationError("Дедлайн должен быть в будущем.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        for name in ("offer", "geo", "description"):
            if name in attrs and not attrs[name].strip():
                raise serializers.ValidationError({name: "Поле не может быть пустым."})
        return attrs


class RevisionRequestSerializer(serializers.Serializer):
    """Body of "request revision": only the comment, nothing else is read from the client."""

    comment = serializers.CharField(max_length=services.MAX_REVISION_COMMENT, trim_whitespace=True)


class TaskEventSerializer(serializers.ModelSerializer):
    actor = UserBriefSerializer(read_only=True)

    class Meta:
        model = TaskEvent
        fields = ["id", "actor", "action", "from_status", "to_status", "payload", "created_at"]
        read_only_fields = fields
