"""Per-endpoint rate limits on the auth API.

Throttles key on client IP (DRF's default ident), so all requests in these
tests come from 127.0.0.1. The throttle history lives in the process-wide
LocMem cache, which persists across tests — clear it before asserting counts.
"""

from rest_framework.test import APIClient

from django.core.cache import cache
from django.test import TestCase, override_settings

EMAIL = "throttle@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class ThrottlingTests(TestCase):
    def setUp(self):
        cache.clear()  # wipe throttle history from earlier tests
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

    @override_settings(AUTH_DEV_OTP="000000")
    @override_settings(AUTH_THROTTLE_LOGIN="2/min")
    def test_login_is_throttled_per_scope(self):
        for _ in range(2):
            response = self.client.post(
                "/api/v1/auth/login/",
                {"email": EMAIL, "password": PASSWORD},
                format="json",
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "TOO_MANY_REQUESTS")

    @override_settings(AUTH_DEV_OTP="000000")
    @override_settings(AUTH_THROTTLE_LOGIN="2/min")
    def test_throttling_does_not_leak_across_scopes(self):
        for _ in range(2):
            response = self.client.post(
                "/api/v1/auth/login/",
                {"email": EMAIL, "password": PASSWORD},
                format="json",
            )
            self.assertEqual(response.status_code, 200)

        # The login scope is spent — the third attempt is blocked.
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 429)

        # Register has its own (default) rate — unaffected by the login limit.
        response = self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": "other@trazeiq.io"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(AUTH_DEV_OTP="000000")
    @override_settings(AUTH_THROTTLE_REGISTER_REQUEST="2/min")
    def test_register_request_is_throttled(self):
        # setUp already requested a code for EMAIL (1 hit); the first new
        # request is the 2nd within the window, the next one trips the 2/min limit.
        response = self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": "new@trazeiq.io"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": "newer@trazeiq.io"},
            format="json",
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["error"]["code"], "TOO_MANY_REQUESTS")
