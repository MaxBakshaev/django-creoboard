import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.tasks.models import ImmutableRecordError, Task, TaskEvent, TaskStatus
from tests.factories import TaskEventFactory, TaskFactory

pytestmark = pytest.mark.django_db


def test_new_task_has_server_defaults():
    task = TaskFactory()

    assert task.status == TaskStatus.NEW
    assert task.assignee is None
    assert task.revision_count == 0
    assert task.created_at is not None and task.created_at.tzinfo is not None
    assert task.taken_at is None
    assert task.submitted_at is None
    assert task.completed_at is None


@pytest.mark.parametrize("quantity", [0, 501])
def test_quantity_out_of_range_is_rejected_by_the_database(quantity):
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskFactory(quantity=quantity)


@pytest.mark.parametrize("quantity", [1, 500])
def test_quantity_bounds_are_allowed(quantity):
    assert TaskFactory(quantity=quantity).pk


def test_tasks_are_ordered_newest_first():
    first = TaskFactory()
    second = TaskFactory()

    assert list(Task.objects.all()) == [second, first]


def test_event_can_be_created():
    event = TaskEventFactory(payload={"a": 1})

    assert TaskEvent.objects.get(pk=event.pk).payload == {"a": 1}


def test_existing_event_cannot_be_saved_again():
    event = TaskEventFactory()
    event.action = "updated"

    with pytest.raises(ImmutableRecordError):
        event.save()

    assert TaskEvent.objects.get(pk=event.pk).action == "created"


def test_event_cannot_be_deleted():
    event = TaskEventFactory()

    with pytest.raises(ImmutableRecordError):
        event.delete()

    assert TaskEvent.objects.filter(pk=event.pk).exists()


def test_queryset_update_and_delete_are_blocked():
    event = TaskEventFactory()

    with pytest.raises(ImmutableRecordError):
        TaskEvent.objects.filter(pk=event.pk).update(action="updated")
    with pytest.raises(ImmutableRecordError):
        TaskEvent.objects.filter(pk=event.pk).delete()
    with pytest.raises(ImmutableRecordError):
        TaskEvent.objects.bulk_update([event], ["action"])

    assert TaskEvent.objects.get(pk=event.pk).action == "created"


def test_task_with_history_cannot_be_deleted():
    event = TaskEventFactory()

    with pytest.raises(ProtectedError):
        event.task.delete()
