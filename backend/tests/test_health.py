import pytest
from django.urls import reverse
from rest_framework.test import APIClient

URL = "/api/v1/health/"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_health_is_public_and_returns_only_status(client):
    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_url_name_resolves():
    assert reverse("health") == URL


def test_health_sets_no_cookies(client):
    response = client.get(URL)

    assert not response.cookies


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_health_rejects_unsafe_methods(client, method):
    response = getattr(client, method)(URL)

    assert response.status_code == 405
