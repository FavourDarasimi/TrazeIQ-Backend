from rest_framework.test import APIClient

from django.test import TestCase, override_settings

EMAIL = "dev@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class LoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.post(
            "/api/v1/auth/register/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )

    def test_login_before_verification_is_forbidden(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "email_not_verified")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_login_after_verification_returns_tokens(self):
        self.client.post(
            "/api/v1/auth/verify/",
            {"email": EMAIL, "otp": "000000"},
            format="json",
        )
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("access", response.data)
        self.assertIn("trazeiq_access", response.cookies)
        self.assertIn("trazeiq_refresh", response.cookies)
        self.assertEqual(response.data["user"]["email_verified"], True)