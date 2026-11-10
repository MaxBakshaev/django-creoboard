"""Status transitions in the service layer (docs/ARCHITECTURE.md, section 7)."""

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.tasks import services
from apps.tasks.models import ImmutableRecordError, Task, TaskAction, TaskEvent, TaskStatus
from tests.factories import TaskFactory, UserFactory

pytestmark = pytest.mark.django_db

ALL_STATUSES = list(TaskStatus)
ACTION_NAMES = [action.value for action in TaskAction]


def call(action: str, task, actor, **extra):
    """Runs a service function by the action name."""
    function = getattr(services, action)
    if action == "request_revision":
        extra.setdefault("comment", "Please fix the logo.")
    return function(task_id=task.pk, actor=actor, **extra)


def task_in(status: TaskStatus, *, assignee=None, **fields):
    """A task in the given status, built through the ORM (tests may; the app may not)."""
    if status != TaskStatus.NEW and assignee is None:
        assignee = UserFactory(creative=True)
    return TaskFactory(status=status, assignee=assignee, **fields)


def valid_actor(action: str, task):
    """The user who may run the action on the task (ignoring the task status)."""
    if action == "take":
        return UserFactory(creative=True)
    if action == "submit":
        # Nobody is the assignee of a "new" task: any creative is refused with 403 there.
        return task.assignee or UserFactory(creative=True)
    return task.buyer


# --- valid transitions --------------------------------------------------------------------


def test_take_assigns_the_creative_and_sets_the_server_timestamp():
    task = TaskFactory()
    creative = UserFactory(creative=True)
    before = timezone.now()

    result = services.take(task_id=task.pk, actor=creative)

    task.refresh_from_db()
    assert (task.status, task.assignee) == (TaskStatus.IN_PROGRESS, creative)
    assert result.status == TaskStatus.IN_PROGRESS
    assert before <= task.taken_at <= timezone.now()
    assert task.submitted_at is None and task.completed_at is None
    assert task.updated_at >= task.taken_at
    (event,) = task.events.all()
    assert (event.action, event.actor) == ("taken", creative)
    assert (event.from_status, event.to_status) == ("new", "in_progress")


def test_submit_moves_to_review_and_sets_submitted_at():
    task = task_in(TaskStatus.IN_PROGRESS)
    before = timezone.now()

    services.submit(task_id=task.pk, actor=task.assignee)

    task.refresh_from_db()
    assert task.status == TaskStatus.REVIEW
    assert before <= task.submitted_at <= timezone.now()
    (event,) = task.events.all()
    assert (event.action, event.actor) == ("submitted", task.assignee)
    assert (event.from_status, event.to_status) == ("in_progress", "review")


def test_accept_completes_the_task_and_sets_completed_at():
    task = task_in(TaskStatus.REVIEW)
    before = timezone.now()

    services.accept(task_id=task.pk, actor=task.buyer)

    task.refresh_from_db()
    assert task.status == TaskStatus.DONE
    assert before <= task.completed_at <= timezone.now()
    (event,) = task.events.all()
    assert (event.action, event.actor) == ("accepted", task.buyer)
    assert (event.from_status, event.to_status) == ("review", "done")


def test_request_revision_counts_the_round_and_stores_the_comment():
    task = task_in(TaskStatus.REVIEW)

    services.request_revision(task_id=task.pk, actor=task.buyer, comment="  Fix the logo.  ")

    task.refresh_from_db()
    assert (task.status, task.revision_count) == (TaskStatus.REVISION, 1)
    assert task.completed_at is None
    (event,) = task.events.all()
    assert (event.action, event.actor) == ("revision_requested", task.buyer)
    assert (event.from_status, event.to_status) == ("review", "revision")
    assert event.payload == {"comment": "Fix the logo."}


def test_full_cycle_with_a_revision_round():
    task = TaskFactory()
    creative = UserFactory(creative=True)

    services.take(task_id=task.pk, actor=creative)
    services.submit(task_id=task.pk, actor=creative)
    services.request_revision(task_id=task.pk, actor=task.buyer, comment="Again, please.")
    services.submit(task_id=task.pk, actor=creative)
    services.accept(task_id=task.pk, actor=task.buyer)

    task.refresh_from_db()
    assert (task.status, task.revision_count, task.assignee) == (TaskStatus.DONE, 1, creative)
    assert all([task.taken_at, task.submitted_at, task.completed_at])
    assert task.taken_at <= task.submitted_at <= task.completed_at
    assert [e.action for e in task.events.all()] == [
        "taken",
        "submitted",
        "revision_requested",
        "submitted",
        "accepted",
    ]


def test_taken_at_is_kept_when_the_task_goes_through_revisions():
    task = TaskFactory()
    creative = UserFactory(creative=True)
    services.take(task_id=task.pk, actor=creative)
    taken_at = Task.objects.get(pk=task.pk).taken_at

    services.submit(task_id=task.pk, actor=creative)
    services.request_revision(task_id=task.pk, actor=task.buyer, comment="Fix.")
    services.submit(task_id=task.pk, actor=creative)

    assert Task.objects.get(pk=task.pk).taken_at == taken_at


def test_submit_from_revision_refreshes_submitted_at():
    task = task_in(TaskStatus.REVISION, submitted_at=timezone.now() - timedelta(days=1))
    old = task.submitted_at

    services.submit(task_id=task.pk, actor=task.assignee)

    task.refresh_from_db()
    assert task.status == TaskStatus.REVIEW
    assert task.submitted_at > old


# --- invalid transitions: wrong status ----------------------------------------------------


@pytest.mark.parametrize("action", ACTION_NAMES)
@pytest.mark.parametrize("status", ALL_STATUSES)
def test_every_action_from_every_status(action, status):
    allowed_from = {
        "take": {TaskStatus.NEW},
        "submit": {TaskStatus.IN_PROGRESS, TaskStatus.REVISION},
        "accept": {TaskStatus.REVIEW},
        "request_revision": {TaskStatus.REVIEW},
    }[action]
    task = task_in(status)
    actor = valid_actor(action, task)

    if status in allowed_from:
        call(action, task, actor)
        assert Task.objects.get(pk=task.pk).status != status
        assert task.events.count() == 1
    else:
        refused = (
            PermissionDenied
            if (action, status) == ("submit", TaskStatus.NEW)
            else (services.TaskConflictError)
        )
        with pytest.raises(refused):
            call(action, task, actor)
        refreshed = Task.objects.get(pk=task.pk)
        assert refreshed.status == status
        assert refreshed.updated_at == task.updated_at
        assert not task.events.exists()


def test_take_refuses_a_task_that_already_has_an_assignee():
    task = TaskFactory(assignee=UserFactory(creative=True))

    with pytest.raises(services.TaskConflictError):
        services.take(task_id=task.pk, actor=UserFactory(creative=True))

    assert not TaskEvent.objects.exists()


def test_take_of_a_missing_task_is_does_not_exist():
    with pytest.raises(Task.DoesNotExist):
        services.take(task_id=999_999, actor=UserFactory(creative=True))


def test_conflict_message_of_take_does_not_describe_the_task():
    task = task_in(TaskStatus.REVIEW)

    with pytest.raises(services.TaskConflictError) as error:
        services.take(task_id=task.pk, actor=UserFactory(creative=True))

    message = str(error.value)
    assert message == services.TAKE_REFUSED_MESSAGE
    for secret in (task.assignee.username, task.offer, task.status, task.buyer.username):
        assert secret not in message


# --- invalid transitions: wrong user ------------------------------------------------------


@pytest.mark.parametrize("action", ACTION_NAMES)
def test_wrong_roles_are_refused(action):
    status = {
        "take": TaskStatus.NEW,
        "submit": TaskStatus.IN_PROGRESS,
        "accept": TaskStatus.REVIEW,
        "request_revision": TaskStatus.REVIEW,
    }[action]
    task = task_in(status)
    right = {"take": "creative", "submit": "creative"}.get(action, "buyer")
    wrong_users = [
        user
        for role, user in {
            "buyer": UserFactory(),
            "creative": UserFactory(creative=True),
            "lead": UserFactory(lead=True),
        }.items()
        if role != right
    ]

    for actor in wrong_users:
        with pytest.raises(PermissionDenied):
            call(action, task, actor)

    refreshed = Task.objects.get(pk=task.pk)
    assert refreshed.status == status
    assert not task.events.exists()


@pytest.mark.parametrize("action", ["submit", "accept", "request_revision"])
def test_other_creative_or_other_buyer_is_refused(action):
    status = TaskStatus.IN_PROGRESS if action == "submit" else TaskStatus.REVIEW
    task = task_in(status)
    stranger = UserFactory(creative=True) if action == "submit" else UserFactory()

    with pytest.raises(PermissionDenied):
        call(action, task, stranger)

    assert Task.objects.get(pk=task.pk).status == status
    assert not task.events.exists()


def test_creative_cannot_submit_a_task_nobody_has_taken():
    task = TaskFactory()

    with pytest.raises(PermissionDenied):
        services.submit(task_id=task.pk, actor=UserFactory(creative=True))


def test_user_without_role_and_anonymous_are_refused():
    owner = get_user_model().objects.create_superuser(username="owner", password="x" * 20)
    task = TaskFactory()

    for actor in (owner, AnonymousUser()):
        with pytest.raises(PermissionDenied):
            services.take(task_id=task.pk, actor=actor)
    assert Task.objects.get(pk=task.pk).status == TaskStatus.NEW


# --- revision comment ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "comment", ["", "   ", "\n\t", None, "x" * (services.MAX_REVISION_COMMENT + 1)]
)
def test_request_revision_needs_a_reasonable_comment(comment):
    task = task_in(TaskStatus.REVIEW)

    with pytest.raises(ValueError):
        services.request_revision(task_id=task.pk, actor=task.buyer, comment=comment)

    assert Task.objects.get(pk=task.pk).status == TaskStatus.REVIEW
    assert not task.events.exists()


def test_comment_of_maximum_length_is_accepted():
    task = task_in(TaskStatus.REVIEW)

    services.request_revision(
        task_id=task.pk, actor=task.buyer, comment="x" * services.MAX_REVISION_COMMENT
    )

    assert Task.objects.get(pk=task.pk).status == TaskStatus.REVISION


# --- atomicity ----------------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTION_NAMES)
def test_transition_is_rolled_back_when_the_event_cannot_be_written(action):
    status = {
        "take": TaskStatus.NEW,
        "submit": TaskStatus.IN_PROGRESS,
        "accept": TaskStatus.REVIEW,
        "request_revision": TaskStatus.REVIEW,
    }[action]
    task = task_in(status)
    actor = valid_actor(action, task)
    before = Task.objects.get(pk=task.pk)

    with (
        mock.patch.object(TaskEvent.objects, "create", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        call(action, task, actor)

    after = Task.objects.get(pk=task.pk)
    assert (after.status, after.assignee_id, after.revision_count) == (
        before.status,
        before.assignee_id,
        before.revision_count,
    )
    assert (after.taken_at, after.submitted_at, after.completed_at) == (
        before.taken_at,
        before.submitted_at,
        before.completed_at,
    )
    assert after.updated_at == before.updated_at


# --- allowed_actions ----------------------------------------------------------------------


def expected_actions(status, role, *, owner: bool, free: bool) -> list[str]:
    if role == "creative" and status == TaskStatus.NEW and free:
        return ["take"]
    if role == "creative" and owner and status in {TaskStatus.IN_PROGRESS, TaskStatus.REVISION}:
        return ["submit"]
    if role == "buyer" and owner and status == TaskStatus.REVIEW:
        return ["accept", "request_revision"]
    return []


@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("role", ["buyer", "creative", "lead"])
@pytest.mark.parametrize("owner", [True, False])
def test_allowed_actions_matrix(status, role, owner):
    task = task_in(status)
    if role == "buyer":
        user = task.buyer if owner else UserFactory()
    elif role == "creative":
        user = task.assignee if (owner and task.assignee) else UserFactory(creative=True)
    else:
        user = UserFactory(lead=True)
    # A creative who is not the assignee is the one who could take a free task.
    free = task.assignee_id is None
    is_owner = owner and (role != "creative" or task.assignee_id is not None)

    assert services.allowed_actions(task, user) == expected_actions(
        status, role, owner=is_owner, free=free
    )


def test_allowed_actions_for_a_user_without_role_is_empty():
    owner = get_user_model().objects.create_superuser(username="owner", password="x" * 20)
    task = TaskFactory()

    assert services.allowed_actions(task, owner) == []
    assert services.allowed_actions(task, AnonymousUser()) == []


def test_allowed_actions_makes_no_queries(django_assert_num_queries):
    task = task_in(TaskStatus.REVIEW)
    buyer = task.buyer

    with django_assert_num_queries(0):
        assert services.allowed_actions(task, buyer) == ["accept", "request_revision"]


@pytest.mark.parametrize("action", ACTION_NAMES)
@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("who", ["author", "assignee", "other_creative", "other_buyer", "lead"])
def test_allowed_actions_agree_with_the_transitions(action, status, who):
    """Whatever allowed_actions offers must work, and nothing else may."""
    task = task_in(status)
    user = {
        "author": task.buyer,
        "assignee": task.assignee or UserFactory(creative=True),
        "other_creative": UserFactory(creative=True),
        "other_buyer": UserFactory(),
        "lead": UserFactory(lead=True),
    }[who]
    offered = action in services.allowed_actions(task, user)

    try:
        call(action, task, user)
        worked = True
    except (PermissionDenied, services.TaskConflictError):
        worked = False

    assert offered == worked


# --- the journal --------------------------------------------------------------------------


def test_events_written_by_transitions_cannot_be_changed_or_deleted():
    task = TaskFactory()
    services.take(task_id=task.pk, actor=UserFactory(creative=True))
    event = task.events.get()

    event.action = "accepted"
    with pytest.raises(ImmutableRecordError):
        event.save()
    with pytest.raises(ImmutableRecordError):
        event.delete()
    with pytest.raises(ImmutableRecordError):
        task.events.update(action="accepted")
    with pytest.raises(ImmutableRecordError):
        task.events.all().delete()

    assert task.events.get().action == "taken"
