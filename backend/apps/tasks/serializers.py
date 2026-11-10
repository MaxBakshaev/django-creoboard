from typing import Any

from django.utils import timezone
from rest_framework import serializers

from apps.users.serializers import UserBriefSerializer

from .models import MAX_QUANTITY, Task, TaskEvent


class TaskSerializer(serializers.ModelSerializer):
    """The brief is writable; status, people, counters and timestamps are server-controlled."""

    buyer = UserBriefSerializer(read_only=True)
    assignee = UserBriefSerializer(read_only=True)

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


class TaskEventSerializer(serializers.ModelSerializer):
    actor = UserBriefSerializer(read_only=True)

    class Meta:
        model = TaskEvent
        fields = ["id", "actor", "action", "from_status", "to_status", "payload", "created_at"]
        read_only_fields = fields
