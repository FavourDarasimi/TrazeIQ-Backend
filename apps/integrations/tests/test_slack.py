"""Phase 4D: Slack OAuth connect + encrypted token storage.

DoD: the connect endpoint exchanges a code for a token stored encrypted at
rest (a raw DB dump must not expose the token); reconnecting replaces it;
owner/admin only; missing app credentials surface as 503.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.events.tests.test_events import create_org, create_project, register_and_login
from apps.organizations.models import Membership

from ..models import SlackIntegration
from ..slack import SlackAPIError, SlackUnavailable

User = get_user_model()


class SlackConnectTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")

        self.roles = {}
        for role in ("viewer", "developer", "admin"):
            client = APIClient()
            register_and_login(client, f"{role}@trazeiq.io")
            Membership.objects.create(
                user=User.objects.get(email=f"{role}@trazeiq.io"),
                organization_id=self.org_id,
                role=role,
            )
            self.roles[role] = client

    def connect(self, client=None, **overrides):
        body = {"organization": self.org_id, "code": "oauth-code"}
        body.update(overrides)
        return (client or self.owner).post(
            "/api/v1/integrations/slack/connect/", body, format="json"
        )

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        return_value={"access_token": "xoxb-secret-token", "team_name": "Acme"},
    )
    def test_connect_stores_token_and_reports_team(self, _exchange):
        response = self.connect()
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertTrue(data["connected"])
        self.assertEqual(data["team_name"], "Acme")

        integration = SlackIntegration.objects.get(organization_id=self.org_id)
        self.assertEqual(integration.access_token, "xoxb-secret-token")
        self.assertEqual(integration.team_name, "Acme")

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        return_value={"access_token": "xoxb-secret-token", "team_name": "Acme"},
    )
    def test_token_is_encrypted_at_rest(self, _exchange):
        self.connect()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_token FROM integrations_slackintegration"
            )
            stored = cursor.fetchone()[0]
        self.assertNotEqual(stored, "xoxb-secret-token")
        self.assertTrue(stored.startswith("trazeiq-enc:"))

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        return_value={"access_token": "xoxb-new", "team_name": "Acme"},
    )
    def test_reconnect_replaces_token(self, _exchange):
        self.connect()
        self.connect(code="new-code")
        integration = SlackIntegration.objects.get(organization_id=self.org_id)
        self.assertEqual(integration.access_token, "xoxb-new")
        self.assertEqual(SlackIntegration.objects.count(), 1)

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        side_effect=SlackUnavailable("creds missing"),
    )
    def test_missing_credentials_is_503(self, _exchange):
        response = self.connect()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data["error"]["code"], "SLACK_NOT_CONFIGURED"
        )

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        side_effect=SlackAPIError("invalid_code"),
    )
    def test_slack_rejection_is_400(self, _exchange):
        response = self.connect()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"]["code"], "SLACK_CONNECT_FAILED"
        )

    def test_requires_code_and_organization(self):
        response = self.owner.post(
            "/api/v1/integrations/slack/connect/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.data["error"]["fields"])
        self.assertIn("organization", response.data["error"]["fields"])

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        return_value={"access_token": "xoxb-secret-token", "team_name": "Acme"},
    )
    def test_unknown_organization_is_404(self, _exchange):
        from uuid import uuid4

        response = self.connect(organization=str(uuid4()))
        self.assertEqual(response.status_code, 404)

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        return_value={"access_token": "xoxb-secret-token", "team_name": "Acme"},
    )
    def test_viewer_and_developer_are_denied(self, _exchange):
        for role in ("viewer", "developer"):
            response = self.connect(client=self.roles[role])
            self.assertEqual(response.status_code, 403)
            self.assertEqual(
                response.data["error"]["code"], "PERMISSION_DENIED"
            )
        self.assertEqual(SlackIntegration.objects.count(), 0)

    @mock.patch(
        "apps.integrations.views.exchange_oauth_code",
        return_value={"access_token": "xoxb-secret-token", "team_name": "Acme"},
    )
    def test_admin_can_connect(self, _exchange):
        response = self.connect(client=self.roles["admin"])
        self.assertEqual(response.status_code, 200)


class SlackStatusTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")
        create_project(self.owner, org=self.org_id)

        self.viewer = APIClient()
        register_and_login(self.viewer, "viewer@trazeiq.io")
        Membership.objects.create(
            user=User.objects.get(email="viewer@trazeiq.io"),
            organization_id=self.org_id,
            role="viewer",
        )

    def status(self, client=None):
        return (client or self.owner).get(
            "/api/v1/integrations/slack/status/",
            {"organization": self.org_id},
        )

    def test_reports_not_connected(self):
        response = self.status()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["data"]["connected"])

    def test_reports_connected_after_connect(self):
        SlackIntegration.objects.create(
            organization_id=self.org_id,
            access_token="xoxb-token",
            team_name="Acme",
        )
        response = self.status()
        self.assertTrue(response.data["data"]["connected"])
        self.assertEqual(response.data["data"]["team_name"], "Acme")

    def test_viewer_can_read_status(self):
        response = self.status(client=self.viewer)
        self.assertEqual(response.status_code, 200)

    def test_foreign_org_is_404(self):
        other = APIClient()
        register_and_login(other, "bob@example.io")
        create_org(other, "BobCo")
        response = self.status(client=other)
        self.assertEqual(response.status_code, 404)