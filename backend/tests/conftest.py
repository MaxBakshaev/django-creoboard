import pytest
from django.core.cache import cache
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def _clear_cache():
    """Throttle counters live in the cache; keep tests independent of each other."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def csrf_client() -> APIClient:
    """Client that enforces CSRF like a real browser does."""
    return APIClient(enforce_csrf_checks=True)
