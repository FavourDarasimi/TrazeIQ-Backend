from rest_framework.test import APIClient

from django.test import TestCase, override_settings

from apps.accounts.models import User


@override_settings(GOOGLE_CLIENT_ID="")
class GoogleAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_google_stub_signup_creates_verified_user(self):
        response = self.client.post(
            "/api/v1/auth/google/",
            {"email": "g@trazeiq.io", "name": "Goog"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="g@trazeiq.io")
        self.assertEqual(user.auth_provider, "google")
        self.assertTrue(user.email_verified)
        self.assertTrue(user.is_active)
        self.assertIn("trazeiq_refresh", response.cookies)

    def test_google_stub_second_call_logs_in_same_user(self):
        self.client.post("/api/v1/auth/google/", {"email": "g@trazeiq.io"}, format="json")
        second = self.client.post(
            "/api/v1/auth/google/", {"email": "g@trazeiq.io"}, format="json"
        )
        self.assertEqual(second.status_code, 200)
        count = User.objects.filter(email="g@trazeiq.io").count()
        self.assertEqual(count, 1)