from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Organization, Membership
from apps.projects.models import Project
from apps.events.models import Event, ErrorGroup
from apps.incidents.models import Incident
from apps.incidents.selectors import get_incident_deploy_intelligence
from apps.deployments.models import Release, Deployment


class DeployIntelligenceTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="qa@trazeiq.io", password="StrongPassword123!"
        )
        self.org = Organization.objects.create(name="Acme Corp", owner=self.user)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role="owner",
        )
        self.project = Project.objects.create(
            organization=self.org,
            name="Payment Service",
            api_key_hash="hash-payment",
            api_key_prefix="tr_pay_",
            environment="production",
        )

    def test_deploy_intelligence_regression_attribution(self):
        t_deploy = timezone.now() - timedelta(minutes=45)

        release = Release.objects.create(
            project=self.project,
            version="v1.4.2",
            commit_sha="4f81b37abc",
            commit_message="feat: optimize token cache (#42)",
            commit_author="Devin Vance",
            commit_url="https://github.com/acme/pay/commit/4f81b37abc",
            pull_request_url="https://github.com/acme/pay/pull/42",
            pull_request_title="feat: optimize token cache (#42)",
        )
        deployment = Deployment.objects.create(
            project=self.project,
            release=release,
            environment="production",
            status=Deployment.Status.COMPLETED,
            deployed_at=t_deploy,
        )

        group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp-token-cache-fail",
            title="KeyError: 'cache_token'",
            count=12,
            first_seen=t_deploy + timedelta(minutes=2),
            last_seen=timezone.now(),
        )
        incident = Incident.objects.create(
            project=self.project,
            error_group=group,
            status=Incident.Status.OPEN,
        )

        # Create events after deploy
        for i in range(5):
            Event.objects.create(
                project=self.project,
                error_group=group,
                message="KeyError: 'cache_token'",
                release="v1.4.2",
                endpoint="/api/v1/checkout",
            )

        intel = get_incident_deploy_intelligence(incident)
        self.assertTrue(intel["has_regression"])
        self.assertEqual(intel["likely_release"]["version"], "v1.4.2")
        self.assertEqual(intel["likely_release"]["commit_sha"], "4f81b37abc")
        self.assertEqual(intel["likely_release"]["pull_request_url"], "https://github.com/acme/pay/pull/42")
        self.assertEqual(intel["confidence"], "high")
        self.assertIn("/api/v1/checkout", intel["affected_endpoints"])
        self.assertEqual(intel["error_rate_before"], 0.0)
        self.assertGreater(intel["error_rate_after"], 0.0)

        # Test incident detail API returns deploy_intelligence
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"/api/v1/incidents/{incident.pk}/")
        self.assertEqual(response.status_code, 200)
        inc_data = response.json()["data"]["incident"]
        self.assertIn("deploy_intelligence", inc_data)
        self.assertTrue(inc_data["deploy_intelligence"]["has_regression"])
        self.assertEqual(inc_data["deploy_intelligence"]["likely_release"]["version"], "v1.4.2")

    def test_deploy_intelligence_no_regression(self):
        group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp-old-error",
            title="OldError",
            count=1,
            first_seen=timezone.now() - timedelta(days=7),
            last_seen=timezone.now(),
        )
        incident = Incident.objects.create(
            project=self.project,
            error_group=group,
            status=Incident.Status.OPEN,
        )

        intel = get_incident_deploy_intelligence(incident)
        self.assertFalse(intel["has_regression"])
        self.assertIsNone(intel["likely_release"])
