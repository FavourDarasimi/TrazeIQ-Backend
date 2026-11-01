from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Organization, Membership
from apps.projects.models import Project, ProjectApiKey, ApiKeyScope
from apps.projects.utils import generate_api_key, hash_api_key, api_key_prefix
from apps.events.models import ErrorGroup
from apps.incidents.models import Incident, TimelineEntry
from apps.deployments.models import Release, Deployment


class DeploymentsApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="deployer@trazeiq.io", password="StrongPassword123!"
        )
        self.org = Organization.objects.create(name="Acme Corp", owner=self.user)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role="owner",
        )
        self.raw_key = generate_api_key()
        key_hash = hash_api_key(self.raw_key)
        prefix = api_key_prefix(self.raw_key)
        self.project = Project.objects.create(
            organization=self.org,
            name="Checkout Service",
            api_key_hash=key_hash,
            api_key_prefix=prefix,
            environment="production",
        )
        self.project_api_key = ProjectApiKey.objects.create(
            project=self.project,
            name="Production Ingest",
            api_key_hash=key_hash,
            api_key_prefix=prefix,
            scopes=[ApiKeyScope.INGEST_ONLY],
        )

    def test_record_deployment_via_api_key(self):
        response = self.client.post(
            "/api/v1/deployments/",
            data={
                "release": "v1.4.2",
                "environment": "production",
                "commit_sha": "4f81b37abc",
                "commit_message": "feat: optimized token cache",
                "commit_author": "Devin Vance <devin@trazeiq.io>",
                "commit_url": "https://github.com/acme/checkout/commit/4f81b37abc",
                "pull_request_url": "https://github.com/acme/checkout/pull/42",
                "pull_request_title": "Optimize token cache (#42)",
            },
            HTTP_X_API_KEY=self.raw_key,
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]["deployment"]
        self.assertEqual(data["release"]["version"], "v1.4.2")
        self.assertEqual(data["release"]["commit_sha"], "4f81b37abc")
        self.assertEqual(data["environment"], "production")
        self.assertEqual(Deployment.objects.count(), 1)
        self.assertEqual(Release.objects.count(), 1)

    def test_record_deployment_via_user_session(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/v1/deployments/",
            data={
                "project_id": str(self.project.pk),
                "release": "v1.5.0",
                "environment": "staging",
                "status": "completed",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]["deployment"]
        self.assertEqual(data["release"]["version"], "v1.5.0")
        self.assertEqual(data["environment"], "staging")

    def test_record_deployment_with_incident_fix_in_commit_message(self):
        group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp-incident-fix",
            title="NullPointerException",
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        incident = Incident.objects.create(
            project=self.project,
            error_group=group,
            status=Incident.Status.OPEN,
        )

        response = self.client.post(
            "/api/v1/deployments/",
            data={
                "release": "v1.4.3",
                "commit_sha": "a1b2c3d4",
                "commit_message": f"fix: resolve null pointer in checkout (fixes #{incident.pk})",
            },
            HTTP_X_API_KEY=self.raw_key,
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        # Check incident was marked fix_verified and evidence attached
        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.FIX_VERIFIED)
        timeline = TimelineEntry.objects.filter(incident=incident)
        self.assertTrue(timeline.exists())
        self.assertIn("Fix verified in release v1.4.3", timeline.first().content)

    def test_project_deployments_and_releases_list_endpoints(self):
        self.client.force_authenticate(user=self.user)
        # Create a release and deployment
        rel = Release.objects.create(project=self.project, version="v1.0.0")
        Deployment.objects.create(project=self.project, release=rel, environment="production")

        res_dep = self.client.get(f"/api/v1/projects/{self.project.pk}/deployments/")
        self.assertEqual(res_dep.status_code, 200)
        self.assertEqual(len(res_dep.json()["data"]["deployments"]), 1)

        res_rel = self.client.get(f"/api/v1/projects/{self.project.pk}/releases/")
        self.assertEqual(res_rel.status_code, 200)
        self.assertEqual(len(res_rel.json()["data"]["releases"]), 1)

    def test_github_webhook_deployment_status(self):
        response = self.client.post(
            f"/api/v1/deployments/webhooks/github/?key={self.raw_key}",
            data={
                "deployment": {
                    "id": 12345,
                    "ref": "v2.0.0",
                    "sha": "abcdef123456",
                    "environment": "production",
                },
                "deployment_status": {
                    "state": "success",
                },
                "repository": {
                    "html_url": "https://github.com/acme/checkout",
                },
            },
            format="json",
            HTTP_X_GITHUB_EVENT="deployment_status",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["recorded"])
        self.assertTrue(Deployment.objects.filter(release__version="v2.0.0").exists())

    def test_gitlab_webhook_deployment(self):
        response = self.client.post(
            f"/api/v1/deployments/webhooks/gitlab/?key={self.raw_key}",
            data={
                "object_kind": "deployment",
                "status": "success",
                "environment": "production",
                "ref": "v2.1.0",
                "sha": "789abcdef",
                "commit": {
                    "message": "deploy v2.1.0",
                    "author_name": "Devin",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["recorded"])
        self.assertTrue(Deployment.objects.filter(release__version="v2.1.0").exists())

    def test_record_deployment_management_command(self):
        out = StringIO()
        call_command(
            "record_deployment",
            release="v3.0.0",
            project_id=str(self.project.pk),
            commit_sha="999888777",
            stdout=out,
        )
        self.assertIn("Successfully recorded deployment", out.getvalue())
        self.assertTrue(Deployment.objects.filter(release__version="v3.0.0").exists())
