from rest_framework.test import APIClient

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User

EMAIL = "dev@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()  # reset the per-email signup cap between test cases
        self.client = APIClient()

    @override_settings(AUTH_DEV_OTP="000000")
    def test_login_before_verification_is_forbidden(self):
        # OTP-first signup never leaves a dormant unverified account, but the
        # login guard still applies to any user that ends up unverified.
        User.objects.create_user(email=EMAIL, password=PASSWORD)
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "EMAIL_NOT_VERIFIED")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_login_after_registration_returns_tokens(self):
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
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["success"], True)
        self.assertNotIn("access", response.data)
        self.assertIn("trazeiq_access", response.cookies)
        self.assertIn("trazeiq_refresh", response.cookies)
        self.assertEqual(response.data["data"]["user"]["email_verified"], True)