from rest_framework.test import APIClient

from django.test import TestCase

EMAIL = "dev@trazeiq.io"
PASSWORD = "fdsK9Qop21z!"


COOKIE_AUTH_HEADER = "trazeiq_access"


class SessionTests(TestCase):
    """Token lifetime, refresh rotation, logout blacklisting and cookie-only auth."""

    def setUp(self):
        self.client = APIClient()
        self.client.post(
            "/api/v1/auth/register/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )
        self.client.post(
            "/api/v1/auth/verify/",
            {"email": EMAIL, "otp": "000000"},
            format="json",
        )
        self.login = self.client.post(
            "/api/v1/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            format="json",
        )

    def test_refresh_uses_http_only_cookie(self):
        response = self.client.post("/api/v1/auth/refresh/", format="json")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("access", response.data)
        self.assertIn("trazeiq_access", response.cookies)
        self.assertIn("trazeiq_refresh", response.cookies)

    def test_refresh_without_cookie_is_rejected(self):
        other = APIClient()
        response = other.post("/api/v1/auth/refresh/", format="json")
        self.assertEqual(response.status_code, 401)

    def test_logout_blacklists_refresh(self):
        old_token = self.login.cookies["trazeiq_refresh"].value
        logged_out = self.client.post("/api/v1/auth/logout/", format="json")
        self.assertEqual(logged_out.status_code, 204)

        replayed = APIClient()
        response = replayed.post(
            "/api/v1/auth/refresh/",
            HTTP_COOKIE="trazeiq_refresh=%s" % old_token,
        )
        self.assertEqual(response.status_code, 401)

    def test_me_requires_access_token(self):
        anonymous = APIClient()
        self.assertEqual(
            anonymous.get("/api/v1/auth/me/").status_code, 401
        )
        response = self.client.get(
            "/api/v1/auth/me/",
            HTTP_AUTHORIZATION="Bearer %s"
            % self.login.cookies["trazeiq_access"].value,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], EMAIL)

    def test_login_sets_both_cookies(self):
        self.assertIn("trazeiq_access", self.login.cookies)
        self.assertIn("trazeiq_refresh", self.login.cookies)
        self.assertNotIn("access", self.login.data)

    def test_me_authenticates_with_cookie_only(self):
        access = self.login.cookies["trazeiq_access"].value
        session = APIClient()
        response = session.get(
            "/api/v1/auth/me/", HTTP_COOKIE="trazeiq_access=%s" % access
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], EMAIL)

    def test_logout_clears_both_cookies(self):
        response = self.client.post("/api/v1/auth/logout/", format="json")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.cookies["trazeiq_access"].value, "")
        self.assertEqual(response.cookies["trazeiq_refresh"].value, "")

    def test_refresh_rotates_access_cookie(self):
        before = self.login.cookies["trazeiq_access"].value
        refreshed = self.client.post("/api/v1/auth/refresh/", format="json")
        self.assertEqual(refreshed.status_code, 200)
        after = refreshed.cookies["trazeiq_access"].value
        self.assertNotEqual(before, after)