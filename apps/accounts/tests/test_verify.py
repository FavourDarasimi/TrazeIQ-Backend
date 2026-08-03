from rest_framework.test import APIClient

from django.test import TestCase, override_settings

from apps.accounts.models import User

EMAIL = "dev@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class VerifyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.post(
            "/api/v1/auth/register/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )

    @override_settings(AUTH_DEV_OTP="000000")
    def test_verify_activates_user_and_returns_tokens(self):
        response = self.client.post(
            "/api/v1/auth/verify/",
            {"email": EMAIL, "otp": "000000"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email=EMAIL)
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)
        self.assertNotIn("access", response.data)
        self.assertIn("trazeiq_access", response.cookies)
        self.assertIn("trazeiq_refresh", response.cookies)

    def test_verify_rejects_wrong_code(self):
        response = self.client.post(
            "/api/v1/auth/verify/",
            {"email": EMAIL, "otp": "123456"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)