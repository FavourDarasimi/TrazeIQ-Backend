from rest_framework.test import APIClient

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User

EMAIL = "dev@trazeiq.io"
USERNAME = "dev_trazeiq"
PASSWORD = "fdsK9Qop21z!"


def _register(client, email=EMAIL, username=USERNAME):
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
    return client.post(
        "/api/v1/auth/register/complete/",
        {
            "registration_token": verified.data["data"]["registration_token"],
            "username": username,
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        format="json",
    )


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()  # reset the per-email signup cap between test cases
        self.client = APIClient()

    @override_settings(AUTH_DEV_OTP="000000")
    def test_login_before_verification_is_forbidden(self):
        # OTP-first signup never leaves a dormant unverified account, but the
        # login guard still applies to any user that ends up unverified.
        User.objects.create_user(
            email=EMAIL, password=PASSWORD, username=USERNAME
        )
        for identifier in (EMAIL, USERNAME):
            with self.subTest(identifier=identifier):
                response = self.client.post(
                    "/api/v1/auth/login/",
                    {"identifier": identifier, "password": PASSWORD},
                    format="json",
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data["success"], False)
                self.assertEqual(
                    response.data["error"]["code"], "EMAIL_NOT_VERIFIED"
                )

    @override_settings(AUTH_DEV_OTP="000000")
    def test_login_after_registration_returns_tokens(self):
        _register(self.client)
        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["success"], True)
        self.assertNotIn("access", response.data)
        self.assertIn("trazeiq_access", response.cookies)
        self.assertIn("trazeiq_refresh", response.cookies)
        self.assertEqual(response.data["data"]["user"]["email_verified"], True)
        self.assertEqual(response.data["data"]["user"]["username"], USERNAME)
        self.assertEqual(response.data["data"]["user"]["name"], USERNAME)

    @override_settings(AUTH_DEV_OTP="000000")
    def test_login_by_username(self):
        _register(self.client)
        response = self.client.post(
            "/api/v1/auth/login/",
            {"identifier": USERNAME, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["user"]["email"], EMAIL)
