import pytest

from tests.factories import DEFAULT_PASSWORD, UserFactory
from tests.test_auth import login

pytestmark = pytest.mark.django_db

HIGH = "1000/min"


def bad_login(client, username="someone", **extra):
    return login(client, username, "wrong-password-123", **extra)


def test_ip_throttle_blocks_after_the_limit_whatever_the_username(api_client, settings):
    settings.LOGIN_THROTTLE_IP = "3/min"
    settings.LOGIN_THROTTLE_USER = HIGH

    codes = [bad_login(api_client, f"user-{i}").status_code for i in range(5)]

    assert codes == [400, 400, 400, 429, 429]


def test_throttled_response_carries_retry_after(api_client, settings):
    settings.LOGIN_THROTTLE_IP = "1/min"
    bad_login(api_client)

    response = bad_login(api_client)

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_default_limit_is_five_attempts_per_minute(api_client):
    codes = [bad_login(api_client).status_code for _ in range(7)]

    assert codes == [400] * 5 + [429] * 2


def test_throttle_also_stops_the_correct_password(api_client, settings):
    settings.LOGIN_THROTTLE_IP = "2/min"
    user = UserFactory()
    bad_login(api_client, user.username)
    bad_login(api_client, user.username)

    response = login(api_client, user.username, DEFAULT_PASSWORD)

    assert response.status_code == 429


def test_username_throttle_blocks_guessing_from_many_addresses(api_client, settings):
    settings.LOGIN_THROTTLE_IP = HIGH
    settings.LOGIN_THROTTLE_USER = "3/min"

    codes = [
        bad_login(api_client, "victim", REMOTE_ADDR=f"10.0.0.{i}").status_code for i in range(5)
    ]

    assert codes == [400, 400, 400, 429, 429]


def test_username_throttle_ignores_case_and_surrounding_spaces(api_client, settings):
    settings.LOGIN_THROTTLE_IP = HIGH
    settings.LOGIN_THROTTLE_USER = "2/min"

    codes = [
        bad_login(api_client, name, REMOTE_ADDR=f"10.0.1.{i}").status_code
        for i, name in enumerate(["Victim", "victim ", " VICTIM"])
    ]

    assert codes == [400, 400, 429]


def test_one_blocked_username_does_not_block_other_users(api_client, settings):
    settings.LOGIN_THROTTLE_IP = HIGH
    settings.LOGIN_THROTTLE_USER = "2/min"
    alice = UserFactory()
    for i in range(3):
        bad_login(api_client, "victim", REMOTE_ADDR=f"10.0.2.{i}")

    response = login(api_client, alice.username, REMOTE_ADDR="10.0.2.99")

    assert response.status_code == 200


def test_blocked_address_does_not_block_other_addresses(api_client, settings):
    settings.LOGIN_THROTTLE_IP = "2/min"
    settings.LOGIN_THROTTLE_USER = HIGH
    user = UserFactory()
    for i in range(3):
        bad_login(api_client, f"u{i}", REMOTE_ADDR="10.0.3.1")

    response = login(api_client, user.username, REMOTE_ADDR="10.0.3.2")

    assert response.status_code == 200


def test_spoofed_forwarded_for_does_not_reset_the_limit_without_proxies(api_client, settings):
    settings.LOGIN_THROTTLE_IP = "3/min"
    settings.LOGIN_THROTTLE_USER = HIGH
    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == 0

    codes = [
        bad_login(api_client, f"u{i}", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}").status_code
        for i in range(5)
    ]

    assert codes == [400, 400, 400, 429, 429]


def test_spoofed_forwarded_for_prefix_does_not_reset_the_limit_behind_proxies(api_client, settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 2}
    settings.LOGIN_THROTTLE_IP = "3/min"
    settings.LOGIN_THROTTLE_USER = HIGH

    # The client controls only the leftmost entries; the two proxies append the rest.
    def attempt(i, real_client="198.51.100.7"):
        forwarded = f"203.0.113.{i}, {real_client}, 172.17.0.1"
        return bad_login(api_client, f"u{i}", HTTP_X_FORWARDED_FOR=forwarded).status_code

    assert [attempt(i) for i in range(5)] == [400, 400, 400, 429, 429]
    assert attempt(9, real_client="198.51.100.8") == 400  # another real client is unaffected
