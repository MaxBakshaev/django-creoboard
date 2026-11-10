"""Business logic of tasks: the only place that changes a task and writes its journal.

Every function runs in one transaction, so a task change and its `TaskEvent` are saved
together or not at all. Status transitions (step 5) are added here as well.
"""

import logging
from datetime import datetime
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.users.models import Role

from .models import Task, TaskEvent, TaskEventAction, TaskStatus

logger = logging.getLogger(__name__)

# Fields a buyer sets in the brief; everything else on the task is controlled by the server.
BRIEF_FIELDS = ("offer", "geo", "creative_type", "quantity", "description", "priority", "deadline")
LONG_TEXT_FIELDS = frozenset({"description"})


class TaskConflictError(Exception):
    """The task is no longer in a state that allows the action (API answers 409)."""


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


@transaction.atomic
def create_task(*, buyer, data: dict[str, Any]) -> Task:
    """Creates a task in the "new" column for `buyer` and records the `created` event."""
    if getattr(buyer, "role", "") != Role.BUYER:
        raise PermissionDenied("Задачи создают только байеры.")
    fields = {name: data[name] for name in BRIEF_FIELDS if name in data}
    task = Task.objects.create(buyer=buyer, **fields)
    TaskEvent.objects.create(
        task=task,
        actor=buyer,
        action=TaskEventAction.CREATED,
        to_status=task.status,
        payload={
            "snapshot": {
                name: _jsonable(getattr(task, name))
                for name in BRIEF_FIELDS
                if name != "description"
            }
        },
    )
    logger.info("Task created: id=%s buyer=%s", task.pk, buyer.pk)
    return task


@transaction.atomic
def update_task(*, task_id: int, actor, data: dict[str, Any]) -> Task:
    """Edits the brief of a task that is still "new" and records the `updated` event.

    The row is locked and the status is checked again here: between the permission check in
    the API and this transaction another user may have taken the task (step 5).
    An update that changes nothing writes no event and leaves `updated_at` untouched.
    """
    task = Task.objects.select_for_update().get(pk=task_id)
    # The API checks this too; repeated here so that no other caller can bypass it.
    if getattr(actor, "role", "") != Role.BUYER or task.buyer_id != actor.pk:
        raise PermissionDenied("Править задачу может только её автор.")
    if task.status != TaskStatus.NEW:
        raise TaskConflictError("Задачу можно редактировать только в статусе «Новые».")

    changes: dict[str, dict[str, Any]] = {}
    for name in BRIEF_FIELDS:
        if name not in data:
            continue
        old, new = getattr(task, name), data[name]
        if old != new:
            # Long free text is not copied into the append-only journal on every edit.
            changes[name] = (
                {"changed": True}
                if name in LONG_TEXT_FIELDS
                else {"from": _jsonable(old), "to": _jsonable(new)}
            )
            setattr(task, name, new)
    if not changes:
        return task

    task.save(update_fields=[*changes, "updated_at"])
    TaskEvent.objects.create(
        task=task,
        actor=actor,
        action=TaskEventAction.UPDATED,
        from_status=task.status,
        to_status=task.status,
        payload={"changes": changes},
    )
    logger.info("Task updated: id=%s fields=%s", task.pk, sorted(changes))
    return task
