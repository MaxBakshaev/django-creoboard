"""The take race on a real database: several creatives take one task at the same moment.

`transaction=True` makes the test data visible to other connections; every thread works on
its own connection, so the PostgreSQL row lock decides, not Python.
"""

import threading

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection

from apps.tasks import services
from apps.tasks.models import Task, TaskEvent, TaskStatus
from tests.factories import TaskFactory, UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def take_at_the_same_time(task_id: int, creatives: list) -> list:
    """Runs `services.take` for every creative at once; returns each outcome in order."""
    barrier = threading.Barrier(len(creatives))
    outcomes: list = [None] * len(creatives)

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            outcomes[index] = services.take(task_id=task_id, actor=creatives[index])
        except Exception as error:  # noqa: BLE001 - the outcome is asserted by the test
            outcomes[index] = error
        finally:
            connection.close()  # each thread has its own connection

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(creatives))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a take call hangs"
    return outcomes


@pytest.mark.parametrize("creatives_count", [2, 5])
def test_exactly_one_creative_takes_the_task(creatives_count):
    task = TaskFactory()
    creatives = [UserFactory(creative=True) for _ in range(creatives_count)]

    outcomes = take_at_the_same_time(task.pk, creatives)

    winners = [i for i, outcome in enumerate(outcomes) if isinstance(outcome, Task)]
    losers = [outcome for outcome in outcomes if isinstance(outcome, services.TaskConflictError)]
    assert len(winners) == 1, outcomes
    assert len(losers) == creatives_count - 1, outcomes
    winner = creatives[winners[0]]

    task.refresh_from_db()
    assert (task.status, task.assignee) == (TaskStatus.IN_PROGRESS, winner)
    assert task.taken_at is not None
    (event,) = TaskEvent.objects.filter(task=task)
    assert (event.action, event.actor) == ("taken", winner)


def test_losers_get_the_neutral_conflict_message():
    task = TaskFactory()
    creatives = [UserFactory(creative=True) for _ in range(3)]

    outcomes = take_at_the_same_time(task.pk, creatives)

    losers = [o for o in outcomes if isinstance(o, services.TaskConflictError)]
    assert losers
    assert {str(loser) for loser in losers} == {services.TAKE_REFUSED_MESSAGE}


def test_a_later_take_of_a_taken_task_is_a_conflict():
    task = TaskFactory()
    first, second = UserFactory(creative=True), UserFactory(creative=True)
    services.take(task_id=task.pk, actor=first)

    with pytest.raises(services.TaskConflictError):
        services.take(task_id=task.pk, actor=second)

    task.refresh_from_db()
    assert task.assignee == first
    assert TaskEvent.objects.filter(task=task).count() == 1


def test_only_creatives_take_even_when_racing_with_them():
    task = TaskFactory()
    creative = UserFactory(creative=True)
    buyer = UserFactory()

    outcomes = take_at_the_same_time(task.pk, [creative, buyer])

    assert isinstance(outcomes[0], Task)
    assert isinstance(outcomes[1], PermissionDenied)
    assert Task.objects.get(pk=task.pk).assignee == creative
