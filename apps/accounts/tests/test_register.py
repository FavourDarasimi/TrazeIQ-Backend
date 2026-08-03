from rest_framework.test import APIClient

from django.test import TestCase

from apps.accounts.models import OTPPurpose, User

EMAIL = "dev@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class RegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_register_leaves_user_inactive_and_unverified(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"email": EMAIL, "password": PASSWORD, "name": "Dev"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email=EMAIL)
        self.assertFalse(user.is_active)
        self.assertFalse(user.email_verified)
        otp = user.otp_codes.filter(purpose=OTPPurpose.EMAIL_VERIFICATION).first()
        self.assertIsNotNone(otp)

    def test_register_duplicate_email_conflicts(self):
        first = self.client.post(
            "/api/v1/auth/register/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            "/api/v1/auth/register/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(second.status_code, 409)