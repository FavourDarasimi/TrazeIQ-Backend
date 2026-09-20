"""Brute-force lockout via django-axes.

Axes records failed logins per (IP, account). The login view checks the
lockout before checking credentials, counts failures via the user_login_failed
signal keyed by the resolved account email (so email- and username-based
attempts share one bucket), and resets the counters on a successful login.
"""

from rest_framework.test import APIClient

from axes.handlers.proxy import AxesProxyHandler

from django.core.cache import cache
from django.test import TestCase, override_settings

EMAIL = "lockout@trazeiq.io"
USERNAME = "lockout_trazeiq"
PASSWORD = "fdsK9Qop21z!"


def _register(client, email, username):
    client.post(
        "/api/v1/auth/register/request-otp/",
        {"email": email},
        format="json",
    )
    verified = client.post(
        "/api/v1/auth/register/verify-otp/",
        {"email": email, "otp": "000000"},
        format="json",
    )
    client.post(
        "/api/v1/auth/register/complete/",
        {
            "registration_token": verified.data["data"]["registration_token"],
            "username": username,
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        format="json",
    )


@override_settings(AXES_FAILURE_LIMIT=3)
@override_settings(AXES_COOLOFF_TIME_HOURS=0.5)
class LockoutTests(TestCase):
    def setUp(self):
        AxesProxyHandler.reset_attempts()
        cache.clear()  # reset the per-email signup cap between test cases
        self.client = APIClient()
        _register(self.client, EMAIL, USERNAME)

    def _fail(self, identifier, password="WrongPass!"):
        return self.client.post(
            "/api/v1/auth/login/",
            {"identifier": identifier, "password": password},
            format="json",
        )

    def test_account_locks_after_failure_limit(self):
        for _ in range(3):
            response = self._fail(EMAIL)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.data["error"]["code"], "INVALID_CREDENTIALS")

        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "TOO_MANY_REQUESTS")

    def test_correct_password_is_rejected_while_locked(self):
        self._fail(EMAIL)
        self._fail(EMAIL)
        self._fail(EMAIL)

        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)

    def test_lockout_via_username_shares_email_bucket(self):
        self._fail(USERNAME)
        self._fail(USERNAME)
        self._fail(USERNAME)

        # Correct password via email is still locked: same account bucket.
        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)

    def test_mixed_identifiers_share_one_bucket(self):
        self._fail(EMAIL)
        self._fail(USERNAME)
        self._fail(EMAIL)

        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": USERNAME, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)

    def test_successful_login_resets_attempts(self):
        for _ in range(2):
            self._fail(EMAIL)

        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        # A fresh failure cycle starts over instead of instantly re-locking.
        response = self._fail(EMAIL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "INVALID_CREDENTIALS")

    def test_failures_are_per_username(self):
        other = "other@trazeiq.io"
        _register(self.client, other, "other_trazeiq")
        for _ in range(3):
            self._fail(EMAIL)
        self.client.post(
            "/api/v1/auth/login/",
            {"identifier": EMAIL, "password": PASSWORD},
            format="json",
        )

        # The other account is unaffected by EMAIL's lockout.
        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": other, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
