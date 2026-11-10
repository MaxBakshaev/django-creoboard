import ast
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.tasks.models import Task, TaskEvent, TaskStatus
from tests.factories import DEFAULT_PASSWORD, TaskEventFactory, TaskFactory, UserFactory

pytestmark = pytest.mark.django_db

# Literal paths: see the note in tests/test_auth.py.
LIST_URL = "/api/v1/tasks/"
OTHER_STATUSES = [
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.REVISION,
    TaskStatus.DONE,
]


def detail_url(task) -> str:
    return f"/api/v1/tasks/{task.pk}/"


def events_url(task) -> str:
    return f"/api/v1/tasks/{task.pk}/events/"


def payload(**overrides):
    data = {
        "offer": "Casino X",
        "geo": "DE, AT",
        "creative_type": "video",
        "quantity": 2,
        "description": "Two vertical videos.",
        "priority": "high",
        "deadline": (timezone.now() + timedelta(days=2)).isoformat(),
    }
    return data | overrides


def as_user(api_client, user):
    api_client.force_authenticate(user)
    return api_client


@pytest.fixture
def buyer():
    return UserFactory()


@pytest.fixture
def creative():
    return UserFactory(creative=True)


@pytest.fixture
def lead():
    return UserFactory(lead=True)


def started(task, assignee, status=TaskStatus.IN_PROGRESS):
    """A task in work, built through the ORM (tests may; the app changes status in services)."""
    task.status = status
    task.assignee = assignee
    task.save()
    return task


# --- authentication -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url_of"),
    [
        ("get", lambda t: LIST_URL),
        ("post", lambda t: LIST_URL),
        ("get", detail_url),
        ("patch", detail_url),
        ("get", events_url),
    ],
)
def test_anonymous_gets_401(api_client, method, url_of):
    task = TaskFactory()

    response = getattr(api_client, method)(url_of(task), {}, format="json")

    assert response.status_code == 401


def test_user_without_role_gets_403_everywhere(api_client):
    owner = get_user_model().objects.create_superuser(username="owner", password=DEFAULT_PASSWORD)
    task = TaskFactory()
    client = as_user(api_client, owner)

    assert client.get(LIST_URL).status_code == 403
    assert client.post(LIST_URL, payload(), format="json").status_code == 403
    assert client.get(detail_url(task)).status_code == 403
    assert client.patch(detail_url(task), {"offer": "x"}, format="json").status_code == 403
    assert client.get(events_url(task)).status_code == 403


# --- create -------------------------------------------------------------------------------


def test_buyer_creates_task_and_server_sets_the_rest(api_client, buyer):
    response = as_user(api_client, buyer).post(LIST_URL, payload(), format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "new"
    assert body["buyer"]["id"] == buyer.pk
    assert body["assignee"] is None
    assert body["revision_count"] == 0
    assert body["taken_at"] is None and body["submitted_at"] is None
    assert body["completed_at"] is None
    assert body["created_at"] is not None
    task = Task.objects.get(pk=body["id"])
    assert task.buyer == buyer


def test_create_ignores_server_controlled_fields(api_client, buyer, creative):
    hostile = payload(
        status="done",
        buyer=creative.pk,
        assignee=creative.pk,
        revision_count=7,
        created_at="2001-01-01T00:00:00Z",
        taken_at="2001-01-01T00:00:00Z",
        submitted_at="2001-01-01T00:00:00Z",
        completed_at="2001-01-01T00:00:00Z",
        id=999999,
    )

    response = as_user(api_client, buyer).post(LIST_URL, hostile, format="json")

    assert response.status_code == 201
    task = Task.objects.get(pk=response.json()["id"])
    assert task.buyer == buyer
    assert task.status == TaskStatus.NEW
    assert task.assignee is None
    assert task.revision_count == 0
    assert task.pk != 999999
    assert task.created_at.year == timezone.now().year
    assert task.taken_at is None and task.submitted_at is None and task.completed_at is None


def test_create_writes_created_event(api_client, buyer):
    response = as_user(api_client, buyer).post(LIST_URL, payload(), format="json")

    task = Task.objects.get(pk=response.json()["id"])
    (event,) = task.events.all()
    assert (event.action, event.actor, event.to_status) == ("created", buyer, "new")


@pytest.mark.parametrize("role_trait", ["creative", "lead"])
def test_only_buyer_can_create(api_client, role_trait):
    user = UserFactory(**{role_trait: True})

    response = as_user(api_client, user).post(LIST_URL, payload(), format="json")

    assert response.status_code == 403
    assert not Task.objects.exists()
    assert not TaskEvent.objects.exists()


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"offer": ""}, "offer"),
        ({"offer": "   "}, "offer"),
        ({"offer": "x" * 201}, "offer"),
        ({"geo": ""}, "geo"),
        ({"geo": "x" * 65}, "geo"),
        ({"creative_type": "gif"}, "creative_type"),
        ({"creative_type": ""}, "creative_type"),
        ({"quantity": 0}, "quantity"),
        ({"quantity": 501}, "quantity"),
        ({"quantity": -1}, "quantity"),
        ({"quantity": "many"}, "quantity"),
        ({"description": ""}, "description"),
        ({"description": "x" * 5001}, "description"),
        ({"priority": "asap"}, "priority"),
        ({"deadline": "tomorrow"}, "deadline"),
        ({"deadline": (timezone.now() - timedelta(hours=1)).isoformat()}, "deadline"),
    ],
)
def test_create_validation(api_client, buyer, overrides, field):
    response = as_user(api_client, buyer).post(LIST_URL, payload(**overrides), format="json")

    assert response.status_code == 400
    assert field in response.json()
    assert not Task.objects.exists()


@pytest.mark.parametrize("field", ["offer", "geo", "creative_type", "quantity", "description"])
def test_create_requires_the_brief_fields(api_client, buyer, field):
    data = payload()
    del data[field]

    response = as_user(api_client, buyer).post(LIST_URL, data, format="json")

    assert response.status_code == 400
    assert field in response.json()


def test_create_requires_deadline(api_client, buyer):
    data = payload()
    del data["deadline"]

    response = as_user(api_client, buyer).post(LIST_URL, data, format="json")

    assert response.status_code == 400
    assert "deadline" in response.json()


def test_priority_defaults_to_normal(api_client, buyer):
    data = payload()
    del data["priority"]

    response = as_user(api_client, buyer).post(LIST_URL, data, format="json")

    assert response.status_code == 201
    assert response.json()["priority"] == "normal"


def test_create_requires_csrf_token_for_session_users(csrf_client):
    user = UserFactory()
    csrf_client.get("/api/v1/auth/csrf/")
    token = csrf_client.cookies["csrftoken"].value
    login = csrf_client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": DEFAULT_PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert login.status_code == 200
    token = csrf_client.cookies["csrftoken"].value

    without = csrf_client.post(LIST_URL, payload(), format="json")
    with_token = csrf_client.post(LIST_URL, payload(), format="json", HTTP_X_CSRFTOKEN=token)

    assert without.status_code == 403
    assert with_token.status_code == 201


# --- visibility (ARCHITECTURE, section 5) -------------------------------------------------


def test_buyer_sees_only_own_tasks(api_client, buyer):
    mine = TaskFactory(buyer=buyer)
    TaskFactory()  # somebody else's

    client = as_user(api_client, buyer)
    listing = client.get(LIST_URL).json()

    assert [t["id"] for t in listing["results"]] == [mine.pk]


def test_creative_sees_new_tasks_and_own_assigned_ones(api_client, creative):
    other_creative = UserFactory(creative=True)
    new_task = TaskFactory()
    own = started(TaskFactory(), creative)
    own_done = started(TaskFactory(), creative, TaskStatus.DONE)
    foreign = started(TaskFactory(), other_creative)
    foreign_done = started(TaskFactory(), other_creative, TaskStatus.DONE)

    listing = as_user(api_client, creative).get(LIST_URL).json()

    ids = {t["id"] for t in listing["results"]}
    assert ids == {new_task.pk, own.pk, own_done.pk}
    assert foreign.pk not in ids and foreign_done.pk not in ids


def test_lead_sees_all_tasks(api_client, lead, creative):
    tasks = [
        TaskFactory(),
        started(TaskFactory(), creative),
        started(TaskFactory(), creative, TaskStatus.DONE),
    ]

    listing = as_user(api_client, lead).get(LIST_URL).json()

    assert {t["id"] for t in listing["results"]} == {t.pk for t in tasks}


def test_buyer_cannot_read_a_foreign_task(api_client, buyer):
    foreign = TaskFactory()

    response = as_user(api_client, buyer).get(detail_url(foreign))

    assert response.status_code == 404


def test_creative_cannot_read_a_task_in_work_of_another_creative(api_client, creative):
    foreign = started(TaskFactory(), UserFactory(creative=True))

    assert as_user(api_client, creative).get(detail_url(foreign)).status_code == 404


def test_creative_can_read_a_new_task_and_own_task(api_client, creative):
    new_task = TaskFactory()
    own = started(TaskFactory(), creative)
    client = as_user(api_client, creative)

    assert client.get(detail_url(new_task)).status_code == 200
    assert client.get(detail_url(own)).status_code == 200


def test_lead_can_read_any_task(api_client, lead, creative):
    task = started(TaskFactory(), creative)

    assert as_user(api_client, lead).get(detail_url(task)).status_code == 200


def test_unknown_task_is_404(api_client, buyer):
    assert as_user(api_client, buyer).get("/api/v1/tasks/999999/").status_code == 404


# --- update -------------------------------------------------------------------------------


def test_author_edits_new_task_and_event_is_written(api_client, buyer):
    task = TaskFactory(buyer=buyer, offer="Old")

    response = as_user(api_client, buyer).patch(
        detail_url(task), {"offer": "New", "quantity": 10}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["offer"] == "New"
    task.refresh_from_db()
    assert (task.offer, task.quantity) == ("New", 10)
    (event,) = task.events.all()
    assert event.action == "updated"
    assert event.actor == buyer
    assert event.payload["changes"]["offer"] == {"from": "Old", "to": "New"}


def test_patch_ignores_server_controlled_fields(api_client, buyer, creative):
    task = TaskFactory(buyer=buyer)

    response = as_user(api_client, buyer).patch(
        detail_url(task),
        {
            "status": "done",
            "buyer": creative.pk,
            "assignee": creative.pk,
            "revision_count": 5,
            "completed_at": "2001-01-01T00:00:00Z",
            "created_at": "2001-01-01T00:00:00Z",
        },
        format="json",
    )

    assert response.status_code == 200
    task.refresh_from_db()
    assert task.status == TaskStatus.NEW
    assert task.buyer == buyer
    assert task.assignee is None
    assert task.revision_count == 0
    assert task.completed_at is None
    assert task.created_at.year == timezone.now().year
    assert not task.events.exists()


def test_patch_without_changes_writes_no_event(api_client, buyer):
    task = TaskFactory(buyer=buyer)

    response = as_user(api_client, buyer).patch(
        detail_url(task), {"offer": task.offer}, format="json"
    )

    assert response.status_code == 200
    assert not task.events.exists()


@pytest.mark.parametrize("status", OTHER_STATUSES)
def test_author_cannot_edit_task_that_is_not_new(api_client, buyer, creative, status):
    task = started(TaskFactory(buyer=buyer, offer="Old"), creative, status)

    response = as_user(api_client, buyer).patch(detail_url(task), {"offer": "New"}, format="json")

    assert response.status_code == 403
    task.refresh_from_db()
    assert task.offer == "Old"
    assert not task.events.exists()


def test_foreign_buyer_gets_404_on_patch_and_nothing_changes(api_client):
    task = TaskFactory(offer="Old")
    intruder = UserFactory()

    response = as_user(api_client, intruder).patch(
        detail_url(task), {"offer": "Hacked"}, format="json"
    )

    assert response.status_code == 404
    task.refresh_from_db()
    assert task.offer == "Old"
    assert not task.events.exists()


def test_creative_gets_403_on_patch_of_visible_task(api_client, creative):
    task = TaskFactory(offer="Old")

    response = as_user(api_client, creative).patch(detail_url(task), {"offer": "x"}, format="json")

    assert response.status_code == 403
    task.refresh_from_db()
    assert task.offer == "Old"


def test_lead_gets_403_on_patch(api_client, lead):
    task = TaskFactory(offer="Old")

    response = as_user(api_client, lead).patch(detail_url(task), {"offer": "x"}, format="json")

    assert response.status_code == 403
    task.refresh_from_db()
    assert task.offer == "Old"


def test_creative_gets_404_on_patch_of_invisible_task(api_client, creative):
    task = started(TaskFactory(), UserFactory(creative=True))

    response = as_user(api_client, creative).patch(detail_url(task), {"offer": "x"}, format="json")

    assert response.status_code == 404


def test_invalid_patch_is_rejected_without_event(api_client, buyer):
    task = TaskFactory(buyer=buyer)

    response = as_user(api_client, buyer).patch(detail_url(task), {"quantity": 0}, format="json")

    assert response.status_code == 400
    assert not task.events.exists()


def test_past_deadline_is_rejected_on_patch_but_unchanged_deadline_is_allowed(api_client, buyer):
    past = timezone.now() - timedelta(days=1)
    task = TaskFactory(buyer=buyer, deadline=past)
    client = as_user(api_client, buyer)

    rejected = client.patch(
        detail_url(task), {"deadline": (past - timedelta(days=1)).isoformat()}, format="json"
    )
    kept = client.patch(
        detail_url(task), {"deadline": past.isoformat(), "offer": "Fixed"}, format="json"
    )

    assert rejected.status_code == 400
    assert kept.status_code == 200


def test_task_leaving_new_during_update_gives_409(api_client, buyer, monkeypatch):
    """Permission check passed, but the task was taken before the locked re-check."""
    task = TaskFactory(buyer=buyer, offer="Old")
    from apps.tasks import views

    real_update = views.services.update_task

    def take_then_update(**kwargs):
        Task.objects.filter(pk=kwargs["task_id"]).update(status=TaskStatus.IN_PROGRESS)
        return real_update(**kwargs)

    monkeypatch.setattr(views.services, "update_task", take_then_update)

    response = as_user(api_client, buyer).patch(detail_url(task), {"offer": "New"}, format="json")

    assert response.status_code == 409
    task.refresh_from_db()
    assert task.offer == "Old"
    assert not task.events.exists()


@pytest.mark.parametrize("method", ["put", "delete"])
def test_put_and_delete_are_not_allowed(api_client, buyer, method):
    task = TaskFactory(buyer=buyer)

    response = getattr(as_user(api_client, buyer), method)(detail_url(task), payload())

    assert response.status_code == 405
    assert Task.objects.filter(pk=task.pk).exists()


# --- history ------------------------------------------------------------------------------


def test_events_are_listed_oldest_first(api_client, buyer):
    task = TaskFactory(buyer=buyer)
    first = TaskEventFactory(task=task, action="created")
    second = TaskEventFactory(task=task, action="updated", payload={"changes": {}})
    TaskEventFactory()  # another task

    response = as_user(api_client, buyer).get(events_url(task))

    assert response.status_code == 200
    body = response.json()
    assert [e["id"] for e in body] == [first.pk, second.pk]
    assert body[0]["actor"]["id"] == buyer.pk
    assert set(body[0]) == {
        "id",
        "actor",
        "action",
        "from_status",
        "to_status",
        "payload",
        "created_at",
    }


def test_events_of_a_foreign_task_are_404_for_buyer(api_client, buyer):
    task = TaskFactory()
    TaskEventFactory(task=task)

    assert as_user(api_client, buyer).get(events_url(task)).status_code == 404


def test_events_of_a_task_in_work_of_another_creative_are_404(api_client, creative):
    task = started(TaskFactory(), UserFactory(creative=True))

    assert as_user(api_client, creative).get(events_url(task)).status_code == 404


def test_lead_and_creative_read_events_of_visible_tasks(api_client, lead, creative):
    task = TaskFactory()
    TaskEventFactory(task=task)

    assert as_user(api_client, lead).get(events_url(task)).status_code == 200
    assert as_user(api_client, creative).get(events_url(task)).status_code == 200


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_events_cannot_be_written_through_the_api(api_client, buyer, method):
    task = TaskFactory(buyer=buyer)

    response = getattr(as_user(api_client, buyer), method)(events_url(task), {}, format="json")

    assert response.status_code == 405


def test_events_url_without_task_is_not_exposed(api_client, buyer):
    assert as_user(api_client, buyer).get("/api/v1/events/").status_code == 404


# --- guard for domain rule 1 --------------------------------------------------------------


def test_status_is_never_assigned_outside_services():
    """Domain rule 1: views, serializers and admin must not assign `.status`."""
    apps_dir = Path(__file__).resolve().parent.parent / "apps"
    guarded = [
        path
        for pattern in ("views.py", "serializers.py", "admin.py")
        for path in apps_dir.glob(f"*/{pattern}")
    ]
    assert guarded, "no files found to check"

    offenders = []
    for path in guarded:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "status":
                    offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    writes = isinstance(node.func, ast.Attribute) and node.func.attr in {
                        "update",
                        "create",
                        "update_or_create",
                    }
                    if kw.arg == "status" and writes:
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
