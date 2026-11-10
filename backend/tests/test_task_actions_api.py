"""Transition endpoints: 401, 403, 404 (IDOR), 409, the `take` exception and server-set times."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.tasks import services
from apps.tasks.models import Task, TaskEvent, TaskStatus
from tests.factories import DEFAULT_PASSWORD, TaskFactory, UserFactory

pytestmark = pytest.mark.django_db

# Literal paths: see the note in tests/test_auth.py.
LIST_URL = "/api/v1/tasks/"
ACTIONS = ["take", "submit", "accept", "request-revision"]
FORGED_TIME = "2000-01-01T00:00:00Z"
FORGED_FIELDS = {
    "status": "done",
    "taken_at": FORGED_TIME,
    "submitted_at": FORGED_TIME,
    "completed_at": FORGED_TIME,
    "created_at": FORGED_TIME,
    "updated_at": FORGED_TIME,
    "revision_count": 99,
    "allowed_actions": ["accept"],
}


def url(task_id, action: str) -> str:
    return f"/api/v1/tasks/{task_id}/{action}/"


def detail_url(task) -> str:
    return f"/api/v1/tasks/{task.pk}/"


def as_user(api_client, user):
    api_client.force_authenticate(user)
    return api_client


def task_in(status: TaskStatus, *, assignee=None, **fields):
    if status != TaskStatus.NEW and assignee is None:
        assignee = UserFactory(creative=True)
    return TaskFactory(status=status, assignee=assignee, **fields)


def post(api_client, user, task_id, action, body=None):
    as_user(api_client, user)
    if action == "request-revision" and body is None:
        body = {"comment": "Please fix the logo."}
    return api_client.post(url(task_id, action), body or {}, format="json")


# --- authentication and roles -------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
def test_anonymous_gets_401(api_client, action):
    task = TaskFactory()

    response = api_client.post(url(task.pk, action), {}, format="json")

    assert response.status_code == 401


@pytest.mark.parametrize("action", ACTIONS)
def test_user_without_role_gets_403(api_client, action):
    owner = get_user_model().objects.create_superuser(username="owner", password=DEFAULT_PASSWORD)
    task = TaskFactory()

    assert post(api_client, owner, task.pk, action).status_code == 403
    assert Task.objects.get(pk=task.pk).status == TaskStatus.NEW


@pytest.mark.parametrize(
    ("action", "wrong_roles"),
    [
        ("take", ["buyer", "lead"]),
        ("submit", ["buyer", "lead"]),
        ("accept", ["creative", "lead"]),
        ("request-revision", ["creative", "lead"]),
    ],
)
def test_wrong_roles_get_403_before_the_task_is_looked_up(api_client, action, wrong_roles):
    task = task_in(TaskStatus.REVIEW)
    users = {"buyer": task.buyer, "creative": task.assignee, "lead": UserFactory(lead=True)}

    for role in wrong_roles:
        assert post(api_client, users[role], task.pk, action).status_code == 403
        # The same answer for a task that does not exist: roles learn nothing from it.
        assert post(api_client, users[role], 999_999, action).status_code == 403

    assert Task.objects.get(pk=task.pk).status == TaskStatus.REVIEW
    assert not task.events.exists()


@pytest.mark.parametrize("action", ACTIONS)
def test_get_and_other_methods_are_not_allowed(api_client, action):
    task = TaskFactory()
    client = as_user(api_client, UserFactory(creative=True))

    for method in ("get", "put", "patch", "delete"):
        assert getattr(client, method)(url(task.pk, action)).status_code == 405


# --- take ---------------------------------------------------------------------------------


def test_creative_takes_a_new_task(api_client):
    task = TaskFactory()
    creative = UserFactory(creative=True)

    response = post(api_client, creative, task.pk, "take")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["assignee"]["id"] == creative.pk
    assert body["taken_at"] is not None
    assert body["allowed_actions"] == ["submit"]
    assert Task.objects.get(pk=task.pk).assignee == creative
    assert [e.action for e in task.events.all()] == ["taken"]


@pytest.mark.parametrize(
    "status",
    [TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.REVISION, TaskStatus.DONE],
)
def test_take_of_a_task_someone_else_took_is_409_not_404(api_client, status):
    task = task_in(status)

    response = post(api_client, UserFactory(creative=True), task.pk, "take")

    assert response.status_code == 409
    assert Task.objects.get(pk=task.pk).assignee == task.assignee
    assert not task.events.exists()


def test_take_of_the_own_taken_task_is_409(api_client):
    creative = UserFactory(creative=True)
    task = task_in(TaskStatus.IN_PROGRESS, assignee=creative)

    assert post(api_client, creative, task.pk, "take").status_code == 409


def test_second_creative_taking_after_the_first_gets_409(api_client):
    task = TaskFactory()
    first, second = UserFactory(creative=True), UserFactory(creative=True)

    assert post(api_client, first, task.pk, "take").status_code == 200
    # The task is no longer visible to the second creative, yet the answer is 409, not 404.
    assert post(api_client, second, task.pk, "take").status_code == 409

    assert Task.objects.get(pk=task.pk).assignee == first
    assert task.events.count() == 1


def test_take_409_body_reveals_nothing_about_the_task(api_client):
    assignee = UserFactory(creative=True, username="secret-assignee", first_name="Hidden")
    buyer = UserFactory(username="secret-buyer")
    task = task_in(TaskStatus.IN_PROGRESS, assignee=assignee, buyer=buyer, offer="Secret offer")

    response = post(api_client, UserFactory(creative=True), task.pk, "take")

    assert response.status_code == 409
    assert response.json() == {"detail": services.TAKE_REFUSED_MESSAGE}
    text = response.content.decode()
    for leaked in (
        "secret-assignee",
        "Hidden",
        "secret-buyer",
        "Secret offer",
        "in_progress",
        "status",
        "assignee",
        str(assignee.pk),
    ):
        assert leaked not in text


def test_take_409_body_is_the_same_whatever_the_status(api_client):
    bodies = set()
    for status in (
        TaskStatus.IN_PROGRESS,
        TaskStatus.REVIEW,
        TaskStatus.REVISION,
        TaskStatus.DONE,
    ):
        task = task_in(status)
        response = post(api_client, UserFactory(creative=True), task.pk, "take")
        assert response.status_code == 409
        bodies.add(response.content)

    assert len(bodies) == 1


@pytest.mark.parametrize("task_id", ["999999", "99999999999999999999", "abc", "-1", "%C2%B2"])
def test_take_of_a_missing_task_is_404(api_client, task_id):
    creative = UserFactory(creative=True)

    assert post(api_client, creative, task_id, "take").status_code == 404


# --- submit -------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [TaskStatus.IN_PROGRESS, TaskStatus.REVISION])
def test_assignee_submits_the_task(api_client, status):
    task = task_in(status)

    response = post(api_client, task.assignee, task.pk, "submit")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "review"
    assert body["submitted_at"] is not None
    assert body["allowed_actions"] == []
    assert task.events.get().action == "submitted"


def test_foreign_creative_cannot_see_or_submit_a_taken_task(api_client):
    task = task_in(TaskStatus.IN_PROGRESS)

    response = post(api_client, UserFactory(creative=True), task.pk, "submit")

    assert response.status_code == 404
    assert Task.objects.get(pk=task.pk).status == TaskStatus.IN_PROGRESS


def test_creative_submitting_a_visible_new_task_gets_403(api_client):
    task = TaskFactory()

    response = post(api_client, UserFactory(creative=True), task.pk, "submit")

    assert response.status_code == 403
    assert Task.objects.get(pk=task.pk).status == TaskStatus.NEW


@pytest.mark.parametrize("status", [TaskStatus.REVIEW, TaskStatus.DONE])
def test_submit_in_a_wrong_status_is_409(api_client, status):
    task = task_in(status)

    assert post(api_client, task.assignee, task.pk, "submit").status_code == 409
    assert not task.events.exists()


# --- accept -------------------------------------------------------------------------------


def test_author_accepts_the_task(api_client):
    task = task_in(TaskStatus.REVIEW)

    response = post(api_client, task.buyer, task.pk, "accept")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["completed_at"] is not None
    assert body["allowed_actions"] == []
    assert task.events.get().action == "accepted"


@pytest.mark.parametrize("action", ["accept", "request-revision"])
def test_another_buyer_gets_404_for_a_foreign_task(api_client, action):
    task = task_in(TaskStatus.REVIEW)

    response = post(api_client, UserFactory(), task.pk, action)

    assert response.status_code == 404
    assert Task.objects.get(pk=task.pk).status == TaskStatus.REVIEW
    assert not task.events.exists()


@pytest.mark.parametrize("action", ["accept", "request-revision"])
@pytest.mark.parametrize(
    "status", [TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVISION, TaskStatus.DONE]
)
def test_buyer_actions_in_a_wrong_status_are_409(api_client, action, status):
    task = task_in(status)

    assert post(api_client, task.buyer, task.pk, action).status_code == 409
    assert Task.objects.get(pk=task.pk).status == status
    assert not task.events.exists()


# --- request revision ---------------------------------------------------------------------


def test_author_requests_a_revision_with_a_comment(api_client):
    task = task_in(TaskStatus.REVIEW)

    response = post(api_client, task.buyer, task.pk, "request-revision", {"comment": " Fix it "})

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["revision_count"]) == ("revision", 1)
    assert body["allowed_actions"] == []
    event = task.events.get()
    assert (event.action, event.payload) == ("revision_requested", {"comment": "Fix it"})
    # The assignee can now submit again.
    detail = as_user(api_client, task.assignee).get(detail_url(task)).json()
    assert detail["allowed_actions"] == ["submit"]


@pytest.mark.parametrize(
    "body",
    [{}, {"comment": ""}, {"comment": "   "}, {"comment": None}, {"comment": "x" * 2001}],
)
def test_request_revision_needs_a_valid_comment(api_client, body):
    task = task_in(TaskStatus.REVIEW)

    response = post(api_client, task.buyer, task.pk, "request-revision", body)

    assert response.status_code == 400
    assert "comment" in response.json()
    assert Task.objects.get(pk=task.pk).status == TaskStatus.REVIEW
    assert not task.events.exists()


# --- the server controls the times and the status -----------------------------------------


@pytest.mark.parametrize(
    ("action", "status", "stamped", "new_status"),
    [
        ("take", TaskStatus.NEW, "taken_at", "in_progress"),
        ("submit", TaskStatus.IN_PROGRESS, "submitted_at", "review"),
        ("accept", TaskStatus.REVIEW, "completed_at", "done"),
        ("request-revision", TaskStatus.REVIEW, None, "revision"),
    ],
)
def test_forged_fields_in_the_request_body_are_ignored(
    api_client, action, status, stamped, new_status
):
    task = task_in(status)
    actor = {
        "take": UserFactory(creative=True),
        "submit": task.assignee,
    }.get(action, task.buyer)
    expected_assignee = actor if action == "take" else task.assignee
    forged = FORGED_FIELDS | {"assignee": UserFactory(creative=True).pk}
    if action == "request-revision":
        forged |= {"comment": "Fix it"}
    untouched = {
        name: getattr(task, name)
        for name in ("created_at", "taken_at", "submitted_at", "completed_at")
        if name != stamped
    }
    before = timezone.now()

    response = post(api_client, actor, task.pk, action, forged)

    assert response.status_code == 200
    task.refresh_from_db()
    assert task.status == new_status
    assert task.assignee == expected_assignee
    assert task.revision_count == (1 if action == "request-revision" else 0)
    assert {name: getattr(task, name) for name in untouched} == untouched
    if stamped:
        assert before <= getattr(task, stamped) <= timezone.now()
    assert before <= task.updated_at <= timezone.now()


def test_patch_ignores_server_controlled_fields(api_client):
    task = TaskFactory(offer="Old")
    created_at = task.created_at

    response = as_user(api_client, task.buyer).patch(
        detail_url(task),
        FORGED_FIELDS | {"offer": "New", "assignee": task.buyer.pk},
        format="json",
    )

    assert response.status_code == 200
    task.refresh_from_db()
    assert task.offer == "New"
    assert task.status == TaskStatus.NEW
    assert task.assignee is None
    assert (task.taken_at, task.submitted_at, task.completed_at) == (None, None, None)
    assert task.revision_count == 0
    assert task.created_at == created_at
    assert task.updated_at.year != 2000


def test_create_ignores_server_controlled_fields(api_client):
    buyer = UserFactory()
    payload = {
        "offer": "Casino X",
        "geo": "DE",
        "creative_type": "video",
        "quantity": 2,
        "description": "Two videos.",
        "priority": "high",
        "deadline": (timezone.now() + timedelta(days=2)).isoformat(),
    }

    response = as_user(api_client, buyer).post(
        LIST_URL, payload | FORGED_FIELDS | {"assignee": buyer.pk}, format="json"
    )

    assert response.status_code == 201
    task = Task.objects.get(pk=response.json()["id"])
    assert task.status == TaskStatus.NEW
    assert (task.assignee, task.taken_at, task.submitted_at, task.completed_at) == (
        None,
        None,
        None,
        None,
    )
    assert task.revision_count == 0
    assert task.created_at.year != 2000


# --- allowed_actions in the API -----------------------------------------------------------


def test_allowed_actions_for_each_role_on_the_task_endpoints(api_client):
    new = TaskFactory()
    review = task_in(TaskStatus.REVIEW)
    working = task_in(TaskStatus.IN_PROGRESS)
    creative = UserFactory(creative=True)
    lead = UserFactory(lead=True)

    def actions(user, task):
        return as_user(api_client, user).get(detail_url(task)).json()["allowed_actions"]

    assert actions(creative, new) == ["take"]
    assert actions(new.buyer, new) == []
    assert actions(lead, new) == []
    assert actions(review.buyer, review) == ["accept", "request_revision"]
    assert actions(review.assignee, review) == []
    assert actions(lead, review) == []
    assert actions(working.assignee, working) == ["submit"]
    assert actions(working.buyer, working) == []


def test_allowed_actions_are_in_the_list_too(api_client):
    review = task_in(TaskStatus.REVIEW)
    TaskFactory(buyer=review.buyer)

    results = as_user(api_client, review.buyer).get(LIST_URL).json()["results"]

    by_id = {item["id"]: item["allowed_actions"] for item in results}
    assert by_id[review.pk] == ["accept", "request_revision"]
    assert sorted(by_id.values(), key=len) == [[], ["accept", "request_revision"]]


def test_allowed_actions_do_not_add_queries_per_task(api_client):
    buyer = UserFactory()
    TaskFactory.create_batch(2, buyer=buyer)
    client = as_user(api_client, buyer)

    with CaptureQueriesContext(connection) as few:
        client.get(LIST_URL)
    TaskFactory.create_batch(8, buyer=buyer)
    with CaptureQueriesContext(connection) as many:
        client.get(LIST_URL)

    assert len(many) == len(few)


def test_allowed_actions_cannot_be_written(api_client):
    task = TaskFactory()
    as_user(api_client, task.buyer).patch(
        detail_url(task), {"allowed_actions": ["take"]}, format="json"
    )

    detail = as_user(api_client, task.buyer).get(detail_url(task)).json()
    assert detail["allowed_actions"] == []


# --- history ------------------------------------------------------------------------------


def test_transitions_show_up_in_the_history_for_whoever_sees_the_task(api_client):
    task = TaskFactory()
    creative = UserFactory(creative=True)
    post(api_client, creative, task.pk, "take")
    post(api_client, creative, task.pk, "submit")
    post(api_client, task.buyer, task.pk, "request-revision", {"comment": "Fix."})

    for viewer in (task.buyer, creative, UserFactory(lead=True)):
        events = as_user(api_client, viewer).get(f"/api/v1/tasks/{task.pk}/events/").json()
        assert [e["action"] for e in events] == ["taken", "submitted", "revision_requested"]
    # A creative who has nothing to do with the taken task does not see it.
    stranger = as_user(api_client, UserFactory(creative=True))
    assert stranger.get(f"/api/v1/tasks/{task.pk}/events/").status_code == 404
    assert TaskEvent.objects.filter(task=task).count() == 3
