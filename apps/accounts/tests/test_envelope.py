"""Lock in the unified response envelope: {success, message, data | error}.

Every endpoint (including DRF-raised errors) must obey this shape, so the
frontend can rely on it unconditionally.
"""

from rest_framework.test import APIClient

from django.test import TestCase, override_settings

EMAIL = "env@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class EnvelopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def register_verified(self, email=EMAIL, password=PASSWORD):
        self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": email},
            format="json",
        )
        verified = self.client.post(
            "/api/v1/auth/register/verify-otp/",
            {"email": email, "otp": "000000"},
            format="json",
        )
        self.client.post(
            "/api/v1/auth/register/complete/",
            {
                "registration_token": verified.data["data"]["registration_token"],
                "password": password,
                "confirm_password": password,
            },
            format="json",
        )

    def test_login_success_has_envelope_with_user_data(self):
        self.register_verified()
        response = self.client.post(
            "/api/v1/auth/login/", {"email": EMAIL, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["success"], True)
        self.assertIsInstance(response.data["message"], str)
        self.assertEqual(response.data["data"]["user"]["email"], EMAIL)
        self.assertNotIn("error", response.data)

    def test_validation_errors_report_fields(self):
        response = self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": "not-an-email"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")
        self.assertIn("email", response.data["error"]["fields"])

    def test_duplicate_email_maps_to_code(self):
        self.register_verified()
        response = self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": EMAIL},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "EMAIL_TAKEN")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_wrong_otp_maps_to_code(self):
        self.client.post(
            "/api/v1/auth/register/request-otp/",
            {"email": EMAIL},
            format="json",
        )
        response = self.client.post(
            "/api/v1/auth/register/verify-otp/",
            {"email": EMAIL, "otp": "123456"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "OTP_INVALID")

    def test_bad_credentials_maps_to_code(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": "TotallyWrong!"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "INVALID_CREDENTIALS")

    def test_unauthenticated_maps_to_code(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "NOT_AUTHENTICATED")

    def test_health_uses_the_envelope(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["success"], True)
        self.assertEqual(response.data["data"], {"status": "ok"})