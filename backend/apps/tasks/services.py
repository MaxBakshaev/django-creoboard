"""Business logic of tasks: the only place that changes a task and writes its journal.

Every function runs in one transaction, so a task change and its `TaskEvent` are saved
together or not at all. The status and its timestamps change here and nowhere else.

Errors of a transition: the wrong role or a task that is not the user's -> `PermissionDenied`
(403, checked first); a task in the wrong status or a lost race -> `TaskConflictError` (409).
"""

import logging
from datetime import datetime
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.users.models import Role

from .models import (
    MAX_ATTACHMENTS_PER_ROUND,
    MAX_NOTE_LENGTH,
    MAX_REFERENCES_PER_TASK,
    ReferenceKind,
    Task,
    TaskAction,
    TaskAttachment,
    TaskEvent,
    TaskEventAction,
    TaskReference,
    TaskStatus,
)
from .uploads import CheckedUpload

logger = logging.getLogger(__name__)

# Fields a buyer sets in the brief; everything else on the task is controlled by the server.
BRIEF_FIELDS = ("offer", "geo", "creative_type", "quantity", "description", "priority", "deadline")
LONG_TEXT_FIELDS = frozenset({"description"})
MAX_REVISION_COMMENT = 2000
# The answer to a refused `take` must not reveal anything about the task (who took it, status).
TAKE_REFUSED_MESSAGE = "Задачу сейчас нельзя взять."
NO_RESULT_MESSAGE = "Добавьте результат работы перед сдачей на проверку."
# Statuses in which the assignee may upload results.
ATTACHMENT_STATUSES = frozenset({TaskStatus.IN_PROGRESS, TaskStatus.REVISION})


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


# --- status transitions -------------------------------------------------------------------

# Statuses each action may start from.
_SOURCE_STATUSES: dict[TaskAction, frozenset[str]] = {
    TaskAction.TAKE: frozenset({TaskStatus.NEW}),
    TaskAction.SUBMIT: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.REVISION}),
    TaskAction.ACCEPT: frozenset({TaskStatus.REVIEW}),
    TaskAction.REQUEST_REVISION: frozenset({TaskStatus.REVIEW}),
}


def _may_act(task: Task, user, action: TaskAction) -> bool:
    """Role and ownership: may this user run the action on this task at all?"""
    role = getattr(user, "role", "")
    user_id = getattr(user, "pk", None)
    if user_id is None:
        return False
    if action == TaskAction.TAKE:
        return role == Role.CREATIVE
    if action == TaskAction.SUBMIT:
        return role == Role.CREATIVE and task.assignee_id == user_id
    return role == Role.BUYER and task.buyer_id == user_id


def allowed_actions(task: Task, user) -> list[str]:
    """Actions the user can run on the task right now (the API serves them to the client).

    Uses the same rules as the transitions below and makes no database queries.
    """
    return [
        action.value
        for action in TaskAction
        if _may_act(task, user, action)
        and task.status in _SOURCE_STATUSES[action]
        and (action != TaskAction.TAKE or task.assignee_id is None)
    ]


def _lock_and_check(task_id: int, actor, action: TaskAction) -> Task:
    """Locks the task row, then checks the user (403) and the current status (409)."""
    task = Task.objects.select_for_update().get(pk=task_id)
    if not _may_act(task, actor, action):
        raise PermissionDenied("Это действие вам недоступно.")
    if task.status not in _SOURCE_STATUSES[action]:
        raise TaskConflictError("Действие недоступно в текущем статусе задачи.")
    return task


def _move(
    task: Task,
    actor,
    *,
    event_action: TaskEventAction,
    to_status: TaskStatus,
    payload: dict[str, Any] | None = None,
    **changes: Any,
) -> Task:
    """Changes the status (and the given fields) and writes the event, inside the caller's
    transaction."""
    from_status = task.status
    task.status = to_status
    for name, value in changes.items():
        setattr(task, name, value)
    task.save(update_fields=["status", *changes, "updated_at"])
    TaskEvent.objects.create(
        task=task,
        actor=actor,
        action=event_action,
        from_status=from_status,
        to_status=to_status,
        payload=payload or {},
    )
    logger.info("Task moved: id=%s %s -> %s by user=%s", task.pk, from_status, to_status, actor.pk)
    return task


@transaction.atomic
def take(*, task_id: int, actor) -> Task:
    """A creative takes a "new" task. Atomic: of two simultaneous calls exactly one wins.

    No lock is needed: a single conditional UPDATE decides. The loser matches 0 rows and gets
    `TaskConflictError` with a message that says nothing about the task. Raises
    `Task.DoesNotExist` only when there is no such task.
    """
    if not _may_act(Task(), actor, TaskAction.TAKE):
        raise PermissionDenied("Это действие вам недоступно.")
    now = timezone.now()
    taken = Task.objects.filter(pk=task_id, status=TaskStatus.NEW, assignee__isnull=True).update(
        status=TaskStatus.IN_PROGRESS,
        assignee=actor,
        taken_at=now,
        updated_at=now,
    )
    if not taken:
        if not Task.objects.filter(pk=task_id).exists():
            raise Task.DoesNotExist
        raise TaskConflictError(TAKE_REFUSED_MESSAGE)
    TaskEvent.objects.create(
        task_id=task_id,
        actor=actor,
        action=TaskEventAction.TAKEN,
        from_status=TaskStatus.NEW,
        to_status=TaskStatus.IN_PROGRESS,
    )
    logger.info("Task taken: id=%s by user=%s", task_id, actor.pk)
    return Task.objects.select_related("buyer", "assignee").get(pk=task_id)


@transaction.atomic
def submit(*, task_id: int, actor) -> Task:
    """The assignee sends the work to review (from "in progress" or "revision")."""
    task = _lock_and_check(task_id, actor, TaskAction.SUBMIT)
    # The task row is locked, so an upload cannot slip in or out between this check and the move.
    if not TaskAttachment.objects.filter(task=task, round=task.revision_count).exists():
        raise TaskConflictError(NO_RESULT_MESSAGE)
    return _move(
        task,
        actor,
        event_action=TaskEventAction.SUBMITTED,
        to_status=TaskStatus.REVIEW,
        submitted_at=timezone.now(),
    )


@transaction.atomic
def accept(*, task_id: int, actor) -> Task:
    """The buyer who created the task accepts the result."""
    task = _lock_and_check(task_id, actor, TaskAction.ACCEPT)
    return _move(
        task,
        actor,
        event_action=TaskEventAction.ACCEPTED,
        to_status=TaskStatus.DONE,
        completed_at=timezone.now(),
    )


@transaction.atomic
def request_revision(*, task_id: int, actor, comment: str) -> Task:
    """The buyer who created the task sends it back for changes; a comment is required."""
    text = (comment or "").strip()
    if not text or len(text) > MAX_REVISION_COMMENT:
        raise ValueError(f"The comment must have 1..{MAX_REVISION_COMMENT} characters.")
    task = _lock_and_check(task_id, actor, TaskAction.REQUEST_REVISION)
    return _move(
        task,
        actor,
        event_action=TaskEventAction.REVISION_REQUESTED,
        to_status=TaskStatus.REVISION,
        payload={"comment": text},
        revision_count=task.revision_count + 1,
    )


# --- references and results (files) ----------------------------------------------------------


def _store(instance: TaskReference | TaskAttachment, upload: CheckedUpload) -> None:
    """Writes the file under its random name and saves the row (inside the caller's
    transaction)."""
    instance.file.save(upload.stored_name, upload.file, save=False)
    instance.save()


def _discard_file(instance: TaskReference | TaskAttachment) -> None:
    """Removes a file whose row did not make it into the database."""
    if instance.file:
        try:
            instance.file.storage.delete(instance.file.name)
        except OSError:
            logger.warning("Could not remove an orphaned upload: %s", instance.file.name)


@transaction.atomic
def add_reference(
    *,
    task_id: int,
    actor,
    url: str = "",
    upload: CheckedUpload | None = None,
    note: str = "",
) -> TaskReference:
    """The buyer who created the task adds a reference: a checked link or a checked file.

    Allowed until the task is done. The caller has already validated the link or the file
    (`apps.tasks.uploads`); exactly one of `url` and `upload` is given.
    """
    if bool(url) == (upload is not None):
        raise ValueError("Exactly one of url and upload is required.")
    task = Task.objects.select_for_update().get(pk=task_id)
    if getattr(actor, "role", "") != Role.BUYER or task.buyer_id != actor.pk:
        raise PermissionDenied("Добавлять референсы может только автор задачи.")
    if task.status == TaskStatus.DONE:
        raise TaskConflictError("К принятой задаче нельзя добавлять референсы.")
    if task.references.count() >= MAX_REFERENCES_PER_TASK:
        raise TaskConflictError(
            f"К задаче можно добавить не больше {MAX_REFERENCES_PER_TASK} референсов."
        )

    reference = TaskReference(task=task, note=note.strip()[:MAX_NOTE_LENGTH])
    if upload is None:
        reference.kind, reference.url = ReferenceKind.LINK, url
    else:
        reference.kind = ReferenceKind.FILE
        reference.original_name = upload.original_name
        reference.size = upload.size
        reference.content_type = upload.content_type
    try:
        if upload is None:
            reference.save()
        else:
            _store(reference, upload)
        TaskEvent.objects.create(
            task=task,
            actor=actor,
            action=TaskEventAction.REFERENCE_ADDED,
            from_status=task.status,
            to_status=task.status,
            payload={
                "reference_id": reference.pk,
                "kind": reference.kind,
                **({"url": url} if upload is None else {"original_name": reference.original_name}),
            },
        )
    except BaseException:
        _discard_file(reference)
        raise
    logger.info("Reference added: id=%s task=%s kind=%s", reference.pk, task.pk, reference.kind)
    return reference


@transaction.atomic
def add_attachment(*, task_id: int, actor, upload: CheckedUpload) -> TaskAttachment:
    """The assignee uploads a result of the current round.

    Allowed only while the task is "in progress" or in "revision". The round is
    `revision_count` read under the row lock, never a client value; the same lock is taken by
    `submit`, so an upload and a submit cannot interleave.
    """
    task = Task.objects.select_for_update().get(pk=task_id)
    if getattr(actor, "role", "") != Role.CREATIVE or task.assignee_id != actor.pk:
        raise PermissionDenied("Загружать результат может только исполнитель задачи.")
    if task.status not in ATTACHMENT_STATUSES:
        raise TaskConflictError(
            "Результат можно загружать только в статусах «В работе» и «Правки»."
        )
    if task.attachments.filter(round=task.revision_count).count() >= MAX_ATTACHMENTS_PER_ROUND:
        raise TaskConflictError(
            f"За один раунд можно загрузить не больше {MAX_ATTACHMENTS_PER_ROUND} файлов."
        )

    attachment = TaskAttachment(
        task=task,
        uploaded_by=actor,
        original_name=upload.original_name,
        size=upload.size,
        content_type=upload.content_type,
        round=task.revision_count,
    )
    try:
        _store(attachment, upload)
        TaskEvent.objects.create(
            task=task,
            actor=actor,
            action=TaskEventAction.ATTACHMENT_ADDED,
            from_status=task.status,
            to_status=task.status,
            payload={
                "attachment_id": attachment.pk,
                "original_name": attachment.original_name,
                "size": attachment.size,
                "round": attachment.round,
            },
        )
    except BaseException:
        _discard_file(attachment)
        raise
    logger.info(
        "Attachment added: id=%s task=%s round=%s", attachment.pk, task.pk, attachment.round
    )
    return attachment
