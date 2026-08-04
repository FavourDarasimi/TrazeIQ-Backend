from rest_framework.test import APIClient

from django.core.cache import cache
from django.test import TestCase, override_settings

EMAIL = "dev@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class PasswordResetTests(TestCase):
    def setUp(self):
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

    def test_forgot_password_unknown_email_is_silent(self):
        response = self.client.post(
            "/api/v1/auth/forgot-password/", {"email": "nobody@nowhere.io"}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(AUTH_DEV_OTP="000000")
    def test_forgot_and_reset_password_flow(self):
        forgot = self.client.post(
            "/api/v1/auth/forgot-password/", {"email": EMAIL}, format="json"
        )
        self.assertEqual(forgot.status_code, 200)

        reset = self.client.post(
            "/api/v1/auth/reset-password/",
            {"email": EMAIL, "otp": "000000", "new_password": "fresh9T!kz2"},
            format="json",
        )
        self.assertEqual(reset.status_code, 200)

        old_login = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(old_login.status_code, 401)

        new_login = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": "fresh9T!kz2"},
            format="json",
        )
        self.assertEqual(new_login.status_code, 200)