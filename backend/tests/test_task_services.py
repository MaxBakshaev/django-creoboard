from datetime import timedelta
from unittest import mock

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.tasks import services
from apps.tasks.models import Task, TaskEvent, TaskStatus
from tests.factories import TaskFactory, UserFactory

pytestmark = pytest.mark.django_db


def brief(**overrides):
    data = {
        "offer": "Casino X",
        "geo": "DE",
        "creative_type": "video",
        "quantity": 2,
        "description": "Two vertical videos.",
        "priority": "high",
        "deadline": timezone.now() + timedelta(days=2),
    }
    return data | overrides


def test_create_task_sets_server_fields_and_writes_created_event():
    buyer = UserFactory()

    task = services.create_task(buyer=buyer, data=brief())

    assert task.buyer == buyer
    assert task.status == TaskStatus.NEW
    assert task.assignee is None
    (event,) = task.events.all()
    assert event.action == "created"
    assert event.actor == buyer
    assert (event.from_status, event.to_status) == ("", "new")
    assert event.payload["snapshot"]["offer"] == "Casino X"
    assert "description" not in event.payload["snapshot"]


def test_create_task_ignores_non_brief_fields():
    buyer = UserFactory()

    task = services.create_task(
        buyer=buyer, data=brief() | {"status": "done", "revision_count": 9, "assignee": buyer}
    )

    assert task.status == TaskStatus.NEW
    assert task.revision_count == 0
    assert task.assignee is None


def test_update_task_changes_fields_and_records_old_and_new_values():
    task = TaskFactory(offer="Old", quantity=3)

    services.update_task(task_id=task.pk, actor=task.buyer, data={"offer": "New", "quantity": 4})

    task.refresh_from_db()
    assert (task.offer, task.quantity) == ("New", 4)
    (event,) = task.events.all()
    assert event.action == "updated"
    assert event.payload == {
        "changes": {
            "offer": {"from": "Old", "to": "New"},
            "quantity": {"from": 3, "to": 4},
        }
    }


def test_update_task_serializes_deadline_to_json():
    task = TaskFactory()
    new_deadline = timezone.now() + timedelta(days=9)

    services.update_task(task_id=task.pk, actor=task.buyer, data={"deadline": new_deadline})

    (event,) = task.events.all()
    assert event.payload["changes"]["deadline"]["to"] == new_deadline.isoformat()


def test_update_without_real_changes_writes_no_event_and_keeps_updated_at():
    task = TaskFactory()
    updated_at = Task.objects.get(pk=task.pk).updated_at

    services.update_task(
        task_id=task.pk, actor=task.buyer, data={"offer": task.offer, "geo": task.geo}
    )

    assert not TaskEvent.objects.exists()
    assert Task.objects.get(pk=task.pk).updated_at == updated_at


@pytest.mark.parametrize(
    "status", [TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.REVISION, TaskStatus.DONE]
)
def test_update_is_refused_when_task_is_not_new(status):
    task = TaskFactory(status=status, offer="Old")

    with pytest.raises(services.TaskConflictError):
        services.update_task(task_id=task.pk, actor=task.buyer, data={"offer": "New"})

    assert Task.objects.get(pk=task.pk).offer == "Old"
    assert not TaskEvent.objects.exists()


def test_task_change_is_rolled_back_when_the_event_cannot_be_written():
    task = TaskFactory(offer="Old")

    with (
        mock.patch.object(TaskEvent.objects, "create", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        services.update_task(task_id=task.pk, actor=task.buyer, data={"offer": "New"})

    assert Task.objects.get(pk=task.pk).offer == "Old"


def test_task_is_not_created_when_the_event_cannot_be_written():
    with (
        mock.patch.object(TaskEvent.objects, "create", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        services.create_task(buyer=UserFactory(), data=brief())

    assert not Task.objects.exists()


def test_update_task_refuses_another_buyer_and_other_roles():
    task = TaskFactory(offer="Old")

    for actor in (UserFactory(), UserFactory(creative=True), UserFactory(lead=True)):
        with pytest.raises(PermissionDenied):
            services.update_task(task_id=task.pk, actor=actor, data={"offer": "New"})

    assert Task.objects.get(pk=task.pk).offer == "Old"
    assert not TaskEvent.objects.exists()


@pytest.mark.parametrize("trait", ["creative", "lead"])
def test_create_task_refuses_non_buyers(trait):
    with pytest.raises(PermissionDenied):
        services.create_task(buyer=UserFactory(**{trait: True}), data=brief())

    assert not Task.objects.exists()


def test_description_change_is_journaled_without_copying_the_text():
    task = TaskFactory(description="A long original text")

    services.update_task(task_id=task.pk, actor=task.buyer, data={"description": "A long new text"})

    (event,) = task.events.all()
    assert event.payload == {"changes": {"description": {"changed": True}}}
