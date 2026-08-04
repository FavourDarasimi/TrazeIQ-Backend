"""OTP-first registration: request-otp -> verify-otp -> complete.

No user row exists until the final step. The registration token is single-use
and only its hash is stored; complete logs the new account straight in.
"""

from rest_framework.test import APIClient

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import OTPCode, OTPPurpose, RegistrationToken, User

EMAIL = "dev@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


class RegistrationFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def request_code(self, email=EMAIL):
        return self.client.post(
            "/api/v1/auth/register/request-otp/", {"email": email}, format="json"
        )

    def verify_code(self, email=EMAIL, otp="000000"):
        return self.client.post(
            "/api/v1/auth/register/verify-otp/",
            {"email": email, "otp": otp},
            format="json",
        )

    def complete(self, token, password=PASSWORD, confirm=PASSWORD):
        return self.client.post(
            "/api/v1/auth/register/complete/",
            {
                "registration_token": token,
                "password": password,
                "confirm_password": confirm,
            },
            format="json",
        )

    def full_register(self, email=EMAIL):
        self.request_code(email)
        verified = self.verify_code(email)
        return self.complete(verified.data["data"]["registration_token"])

    @override_settings(AUTH_DEV_OTP="000000")
    def test_request_otp_creates_no_user_and_emails_code(self):
        response = self.request_code()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["success"], True)
        self.assertIsNone(response.data["data"])
        self.assertFalse(User.objects.filter(email=EMAIL).exists())
        otp = OTPCode.objects.get(purpose=OTPPurpose.EMAIL_VERIFICATION)
        self.assertIsNone(otp.user)
        self.assertEqual(otp.email, EMAIL)

    @override_settings(AUTH_DEV_OTP="000000")
    def test_request_otp_for_existing_account_conflicts(self):
        self.full_register()
        response = self.request_code()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "EMAIL_TAKEN")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_request_otp_rotates_the_prior_code(self):
        self.request_code()
        first = OTPCode.objects.get(purpose=OTPPurpose.EMAIL_VERIFICATION)
        self.request_code()
        second = OTPCode.objects.get(
            purpose=OTPPurpose.EMAIL_VERIFICATION, used_at__isnull=True
        )
        self.assertNotEqual(first.id, second.id)
        first.refresh_from_db()
        self.assertIsNotNone(first.used_at)

    @override_settings(AUTH_DEV_OTP="000000")
    @override_settings(AUTH_REGISTER_EMAIL_CAP=2)
    def test_request_otp_is_capped_per_email(self):
        self.request_code()
        self.request_code()
        response = self.request_code()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["error"]["code"], "TOO_MANY_REQUESTS")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_verify_otp_rejects_wrong_code_and_counts_attempts(self):
        self.request_code()
        response = self.verify_code(otp="123456")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "OTP_INVALID")
        otp = OTPCode.objects.get(purpose=OTPPurpose.EMAIL_VERIFICATION)
        self.assertEqual(otp.attempts, 1)

    @override_settings(AUTH_DEV_OTP="000000")
    @override_settings(AUTH_OTP_MAX_ATTEMPTS=2)
    def test_verify_otp_locks_after_max_attempts(self):
        self.request_code()
        self.verify_code(otp="111111")
        self.verify_code(otp="222222")
        response = self.verify_code(otp="333333")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "OTP_TOO_MANY_ATTEMPTS")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_verify_otp_rejects_expired_code(self):
        # The selector drops expired rows before they reach the consumer, so an
        # expired code surfaces as OTP_INVALID (same as the pre-existing flow).
        self.request_code()
        OTPCode.objects.filter(purpose=OTPPurpose.EMAIL_VERIFICATION).update(
            expires_at="2020-01-01T00:00:00Z"
        )
        response = self.verify_code()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "OTP_INVALID")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_verify_otp_returns_single_use_token_and_hashes_it(self):
        self.request_code()
        response = self.verify_code()
        self.assertEqual(response.status_code, 200)
        raw = response.data["data"]["registration_token"]
        self.assertEqual(len(raw), 64)
        token = RegistrationToken.objects.get(email=EMAIL)
        self.assertNotEqual(token.token_hash, raw)
        self.assertEqual(len(token.token_hash), 64)
        self.assertIsNone(token.used_at)

        # The code is consumed — replaying it mints nothing new.
        replay = self.verify_code()
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(
            RegistrationToken.objects.filter(email=EMAIL).count(), 1
        )

    @override_settings(AUTH_DEV_OTP="000000")
    def test_complete_creates_verified_active_user_and_logs_in(self):
        response = self.full_register()
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email=EMAIL)
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)
        self.assertEqual(user.auth_provider, "email")
        token = RegistrationToken.objects.get(email=EMAIL)
        self.assertIsNotNone(token.used_at)
        self.assertNotIn("access", response.data)
        self.assertIn("trazeiq_access", response.cookies)
        self.assertIn("trazeiq_refresh", response.cookies)
        self.assertEqual(
            response.data["data"]["user"]["email_verified"], True
        )

    @override_settings(AUTH_DEV_OTP="000000")
    def test_complete_rejects_replayed_token(self):
        self.request_code()
        verified = self.verify_code()
        token = verified.data["data"]["registration_token"]
        self.complete(token)
        replay = self.complete(token)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.data["error"]["code"], "REGISTRATION_TOKEN_INVALID")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_complete_rejects_unknown_token(self):
        response = self.complete("0" * 64)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"]["code"], "REGISTRATION_TOKEN_INVALID"
        )

    @override_settings(AUTH_DEV_OTP="000000")
    def test_complete_rejects_expired_token(self):
        self.request_code()
        verified = self.verify_code()
        RegistrationToken.objects.filter(email=EMAIL).update(
            expires_at="2020-01-01T00:00:00Z"
        )
        response = self.complete(verified.data["data"]["registration_token"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"]["code"], "REGISTRATION_TOKEN_EXPIRED"
        )
        self.assertFalse(User.objects.filter(email=EMAIL).exists())

    @override_settings(AUTH_DEV_OTP="000000")
    def test_complete_validates_password_and_confirmation(self):
        self.request_code()
        verified = self.verify_code()
        token = verified.data["data"]["registration_token"]

        short = self.complete(token, password="short")
        self.assertEqual(short.status_code, 400)
        self.assertEqual(short.data["error"]["code"], "VALIDATION_FAILED")
        self.assertIn("password", short.data["error"]["fields"])

        mismatch = self.complete(token, confirm="different!")
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(mismatch.data["error"]["code"], "VALIDATION_FAILED")
        self.assertIn("confirm_password", mismatch.data["error"]["fields"])
        self.assertFalse(User.objects.filter(email=EMAIL).exists())

    @override_settings(AUTH_DEV_OTP="000000")
    def test_complete_conflicts_if_account_was_created_in_the_meantime(self):
        self.request_code()
        verified = self.verify_code()
        User.objects.create_user(
            email=EMAIL,
            password=PASSWORD,
            email_verified=True,
            is_active=True,
        )
        response = self.complete(verified.data["data"]["registration_token"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "EMAIL_TAKEN")

    @override_settings(AUTH_DEV_OTP="000000")
    def test_completed_account_can_log_in_with_chosen_password(self):
        self.full_register()
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
