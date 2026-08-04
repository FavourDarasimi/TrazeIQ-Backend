"""Brute-force lockout via django-axes.

Axes records failed logins per (IP, username). The login view checks the
lockout before checking credentials, counts failures via the user_login_failed
signal, and resets the counters on a successful login.
"""

from rest_framework.test import APIClient

from axes.handlers.proxy import AxesProxyHandler

from django.core.cache import cache
from django.test import TestCase, override_settings

EMAIL = "lockout@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


@override_settings(AXES_FAILURE_LIMIT=3)
@override_settings(AXES_COOLOFF_TIME_HOURS=0.5)
class LockoutTests(TestCase):
    def setUp(self):
        AxesProxyHandler.reset_attempts()
        cache.clear()  # reset the per-email signup cap between test cases
        self.client = APIClient()
        self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": EMAIL},
            format="json",
        )
        verified = self.client.post(
            "/api/v1/auth/register/verify-otp/",
            {"email": EMAIL, "otp": "000000"},
            format="json",
        )
        self.client.post(
            "/api/v1/auth/register/complete/",
            {
                "registration_token": verified.data["data"]["registration_token"],
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
            format="json",
        )

    def test_account_locks_after_failure_limit(self):
        for _ in range(3):
            response = self.client.post(
                "/api/v1/auth/login/",
                {"email": EMAIL, "password": "WrongPass!"},
                format="json",
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.data["error"]["code"], "INVALID_CREDENTIALS")

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "TOO_MANY_REQUESTS")

    def test_correct_password_is_rejected_while_locked(self):
        self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": "WrongPass!"},
            format="json",
        )
        self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": "WrongPass!"},
            format="json",
        )
        self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": "WrongPass!"},
            format="json",
        )

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)

    def test_successful_login_resets_attempts(self):
        for _ in range(2):
            self.client.post(
                "/api/v1/auth/login/",
                {"email": EMAIL, "password": "WrongPass!"},
                format="json",
            )

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        # A fresh failure cycle starts over instead of instantly re-locking.
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": "WrongPass!"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "INVALID_CREDENTIALS")

    def test_failures_are_per_username(self):
        other = "other@trazeiq.io"
        self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": other},
            format="json",
        )
        verified = self.client.post(
            "/api/v1/auth/register/verify-otp/",
            {"email": other, "otp": "000000"},
            format="json",
        )
        self.client.post(
            "/api/v1/auth/register/complete/",
            {
                "registration_token": verified.data["data"]["registration_token"],
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
            format="json",
        )
        for _ in range(3):
            self.client.post(
                "/api/v1/auth/login/",
                {"email": EMAIL, "password": "WrongPass!"},
                format="json",
            )
        self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )

        # The other account is unaffected by EMAIL's lockout.
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": other, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)