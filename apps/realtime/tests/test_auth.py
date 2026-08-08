"""Phase 3A: Pusher channel-auth endpoint.

DoD verification:
- a user with no membership on a project cannot obtain a valid auth
  signature for that project's channel,
- members get a signed payload (HMAC over channel + socket id),
- unauthenticated callers get 401, malformed/unknown channels get 403.
"""

from uuid import uuid4

from django.test import override_settings
from rest_framework.test import APIClient
from django.test import TestCase

from apps.accounts.models import User
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.projects.models import Project

PASSWORD = "Password123!"
AUTH_CREDS = dict(
    PUSHER_APP_ID="123456",
    PUSHER_KEY="test-key",
    PUSHER_SECRET="test-secret",
)


class PusherAuthTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com", password=PASSWORD, email_verified=True
        )
        self.org = Organization.objects.create(name="Acme Inc", owner=self.owner)
        Membership.objects.create(
            user=self.owner, organization=self.org, role=MembershipRole.OWNER
        )
        self.project = Project.objects.create(
            organization=self.org, name="Frontend App"
        )

        self.other = User.objects.create_user(
            email="other@example.com", password=PASSWORD, email_verified=True
        )
        self.other_org = Organization.objects.create(
            name="Rivals Ltd", owner=self.other
        )
        Membership.objects.create(
            user=self.other, organization=self.other_org, role=MembershipRole.OWNER
        )

        self.client = APIClient()

    def _channel(self, project) -> str:
        return f"private-project-{project.id}"

    def _auth(self, channel_name, socket_id="123.456"):
        return self.client.post(
            "/api/v1/pusher/auth/",
            {"channel_name": channel_name, "socket_id": socket_id},
            format="json",
        )

    @override_settings(**AUTH_CREDS)
    def test_member_gets_signed_auth(self):
        self.client.force_authenticate(user=self.owner)
        response = self._auth(self._channel(self.project))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        auth = body["data"]["auth"]
        self.assertTrue(auth.startswith(f"{AUTH_CREDS['PUSHER_KEY']}:"))
        self.assertEqual(len(auth.split(":")[1]), 64)  # hexdigest

    @override_settings(**AUTH_CREDS)
    def test_signature_is_derived_from_channel_and_socket_id(self):
        self.client.force_authenticate(user=self.owner)
        first = self._auth(self._channel(self.project), "123.456").json()["data"]["auth"]
        second = self._auth(self._channel(self.project), "999.888").json()["data"]["auth"]
        self.assertNotEqual(first, second)

    @override_settings(**AUTH_CREDS)
    def test_non_member_is_rejected_with_403(self):
        self.client.force_authenticate(user=self.other)
        response = self._auth(self._channel(self.project))
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"]["code"], "PERMISSION_DENIED")

    @override_settings(**AUTH_CREDS)
    def test_unknown_project_channel_is_rejected_with_403(self):
        self.client.force_authenticate(user=self.owner)
        response = self._auth(f"private-project-{uuid4()}")
        self.assertEqual(response.status_code, 403)

    @override_settings(**AUTH_CREDS)
    def test_malformed_channel_name_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        for bad in ("private-project-notauuid", "project-abc", "public-abc", f"private-{uuid4()}"):
            response = self._auth(bad)
            self.assertEqual(response.status_code, 403, bad)

    def test_unauthenticated_is_rejected_with_401(self):
        response = self._auth(self._channel(self.project))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "NOT_AUTHENTICATED")

    @override_settings(**AUTH_CREDS)
    def test_missing_fields_are_rejected_with_400(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/v1/pusher/auth/", {"channel_name": self._channel(self.project)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_FAILED")

    @override_settings(**AUTH_CREDS)
    def test_form_encoded_payload_is_supported(self):
        """pusher-js's ajax transport sends x-www-form-urlencoded, not JSON."""
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/v1/pusher/auth/",
            data=f"channel_name={self._channel(self.project)}&socket_id=123.456",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("auth", response.json()["data"])

    @override_settings(PUSHER_APP_ID="", PUSHER_KEY="", PUSHER_SECRET="")
    def test_unconfigured_pusher_returns_503(self):
        self.client.force_authenticate(user=self.owner)
        response = self._auth(self._channel(self.project))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "PUSHER_NOT_CONFIGURED")
