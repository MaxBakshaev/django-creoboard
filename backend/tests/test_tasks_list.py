from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.tasks.models import TaskStatus
from tests.factories import TaskFactory, UserFactory

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/tasks/"


@pytest.fixture
def lead_client(api_client):
    api_client.force_authenticate(UserFactory(lead=True))
    return api_client


def ids(response) -> list[int]:
    assert response.status_code == 200, response.content
    return [task["id"] for task in response.json()["results"]]


def test_default_order_is_newest_first(lead_client):
    first, second, third = TaskFactory(), TaskFactory(), TaskFactory()

    assert ids(lead_client.get(LIST_URL)) == [third.pk, second.pk, first.pk]


def test_pagination(lead_client):
    TaskFactory.create_batch(25)

    first = lead_client.get(LIST_URL).json()
    second = lead_client.get(LIST_URL, {"page": 2}).json()
    small = lead_client.get(LIST_URL, {"page_size": 5}).json()
    huge = lead_client.get(LIST_URL, {"page_size": 1000}).json()

    assert first["count"] == 25
    assert len(first["results"]) == 20 and first["next"] is not None
    assert len(second["results"]) == 5 and second["next"] is None
    assert len(small["results"]) == 5
    assert len(huge["results"]) == 25  # capped at 100, there are only 25
    assert lead_client.get(LIST_URL, {"page": 99}).status_code == 404


def test_filter_by_status(lead_client):
    new_task = TaskFactory()
    TaskFactory(status=TaskStatus.REVIEW)

    assert ids(lead_client.get(LIST_URL, {"status": "new"})) == [new_task.pk]
    assert lead_client.get(LIST_URL, {"status": "nope"}).status_code == 400


def test_filter_by_priority_and_type(lead_client):
    urgent = TaskFactory(priority="urgent", creative_type="html5")
    TaskFactory(priority="low", creative_type="static")

    assert ids(lead_client.get(LIST_URL, {"priority": "urgent"})) == [urgent.pk]
    assert ids(lead_client.get(LIST_URL, {"creative_type": "html5"})) == [urgent.pk]


def test_filter_by_assignee(lead_client):
    creative = UserFactory(creative=True)
    mine = TaskFactory(status=TaskStatus.IN_PROGRESS, assignee=creative)
    TaskFactory()

    assert ids(lead_client.get(LIST_URL, {"assignee": creative.pk})) == [mine.pk]
    assert ids(lead_client.get(LIST_URL, {"assignee": 999999})) == []
    assert lead_client.get(LIST_URL, {"assignee": "abc"}).status_code == 400


def test_filter_by_geo_is_partial_and_case_insensitive(lead_client):
    de_at = TaskFactory(geo="DE, AT")
    TaskFactory(geo="PL")

    assert ids(lead_client.get(LIST_URL, {"geo": "at"})) == [de_at.pk]


def test_filter_overdue(lead_client):
    now = timezone.now()
    overdue = TaskFactory(deadline=now - timedelta(days=1))
    overdue_in_work = TaskFactory(deadline=now - timedelta(hours=1), status=TaskStatus.REVISION)
    late_but_done = TaskFactory(deadline=now - timedelta(days=2), status=TaskStatus.DONE)
    on_time = TaskFactory(deadline=now + timedelta(days=1))

    assert set(ids(lead_client.get(LIST_URL, {"overdue": "true"}))) == {
        overdue.pk,
        overdue_in_work.pk,
    }
    assert set(ids(lead_client.get(LIST_URL, {"overdue": "false"}))) == {
        late_but_done.pk,
        on_time.pk,
    }


def test_search_by_offer_and_description(lead_client):
    by_offer = TaskFactory(offer="Casino Royale", description="banners")
    by_description = TaskFactory(offer="Other", description="Needs a ROYALE style")
    TaskFactory(offer="Nothing", description="here")

    found = ids(lead_client.get(LIST_URL, {"search": "royale"}))

    assert set(found) == {by_offer.pk, by_description.pk}
    assert len(ids(lead_client.get(LIST_URL, {"search": "  "}))) == 3


def test_search_treats_wildcards_as_plain_text(lead_client):
    TaskFactory(offer="100% bonus")
    TaskFactory(offer="plain")

    assert len(ids(lead_client.get(LIST_URL, {"search": "%"}))) == 1


def test_ordering_by_deadline_both_directions(lead_client):
    now = timezone.now()
    soon = TaskFactory(deadline=now + timedelta(days=1))
    later = TaskFactory(deadline=now + timedelta(days=5))

    assert ids(lead_client.get(LIST_URL, {"ordering": "deadline"})) == [soon.pk, later.pk]
    assert ids(lead_client.get(LIST_URL, {"ordering": "-deadline"})) == [later.pk, soon.pk]


def test_ordering_by_unknown_or_sensitive_field_is_rejected(lead_client):
    assert lead_client.get(LIST_URL, {"ordering": "buyer__password"}).status_code == 400
    assert lead_client.get(LIST_URL, {"ordering": "priority"}).status_code == 400


def test_filters_never_widen_visibility(api_client):
    buyer = UserFactory()
    other = UserFactory()
    mine = TaskFactory(buyer=buyer, offer="shared word")
    TaskFactory(buyer=other, offer="shared word")
    api_client.force_authenticate(buyer)

    assert ids(api_client.get(LIST_URL, {"search": "shared"})) == [mine.pk]
    assert ids(api_client.get(LIST_URL, {"status": "new", "overdue": "false"})) == [mine.pk]


def test_creative_filter_by_foreign_assignee_leaks_nothing(api_client):
    creative = UserFactory(creative=True)
    other = UserFactory(creative=True)
    TaskFactory(status=TaskStatus.IN_PROGRESS, assignee=other)
    api_client.force_authenticate(creative)

    assert ids(api_client.get(LIST_URL, {"assignee": other.pk})) == []


def test_list_query_count_does_not_grow_with_the_number_of_tasks(lead_client):
    creative = UserFactory(creative=True)
    TaskFactory.create_batch(2, status=TaskStatus.IN_PROGRESS, assignee=creative)
    with CaptureQueriesContext(connection) as few:
        lead_client.get(LIST_URL)

    TaskFactory.create_batch(10, status=TaskStatus.IN_PROGRESS, assignee=creative)
    with CaptureQueriesContext(connection) as many:
        lead_client.get(LIST_URL)

    assert len(many) == len(few)


@pytest.mark.parametrize("value", ["1.5", "1E+999", "0", "-1", "99999999999", "abc"])
def test_assignee_filter_accepts_only_bounded_integers(lead_client, value):
    assert lead_client.get(LIST_URL, {"assignee": value}).status_code == 400


def test_list_filters_do_not_affect_detail_and_events(lead_client):
    task = TaskFactory()

    assert lead_client.get(f"{LIST_URL}{task.pk}/", {"status": "done"}).status_code == 200
    assert lead_client.get(f"{LIST_URL}{task.pk}/events/", {"priority": "bad"}).status_code == 200
