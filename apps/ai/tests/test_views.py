"""Phase 2C: Integration tests for incident AI analysis retrieval and manual re-analysis.

Verifies:
- GET /api/v1/incidents/{id}/analysis/ returns 404 for unknown/foreign incidents or when no analysis exists
- GET /api/v1/incidents/{id}/analysis/ returns 200 with pending/ready/failed state
- POST /api/v1/incidents/{id}/analyze/ triggers re-analysis bypassing cache window
- Tenant isolation is respected on both endpoints
"""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient


from apps.accounts.models import User
from apps.events.models import ErrorGroup
from apps.incidents.models import Incident
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.projects.models import Project

from ..models import AIAnalysis

PASSWORD = "Password123!"


class AIViewsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password=PASSWORD,
            email_verified=True,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password=PASSWORD,
            email_verified=True,
        )

        # User's org, project, error group, and incident
        self.org = Organization.objects.create(name="Acme Inc", owner=self.user)
        Membership.objects.create(
            user=self.user, organization=self.org, role=MembershipRole.OWNER
        )
        self.project = Project.objects.create(
            organization=self.org, name="Frontend App"
        )
        now = timezone.now()
        self.group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp123",
            title="TypeError: undefined is not a function",
            first_seen=now,
            last_seen=now,
        )
        self.incident = Incident.objects.create(
            project=self.project, error_group=self.group
        )

        # Other user's org & incident for tenant isolation checks
        self.other_org = Organization.objects.create(
            name="Other Corp", owner=self.other_user
        )
        Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=MembershipRole.OWNER,
        )
        self.other_project = Project.objects.create(
            organization=self.other_org, name="Backend App"
        )
        self.other_group = ErrorGroup.objects.create(
            project=self.other_project,
            fingerprint="fp456",
            title="ValueError: invalid literal",
            first_seen=now,
            last_seen=now,
        )

        self.other_incident = Incident.objects.create(
            project=self.other_project, error_group=self.other_group
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_unauthenticated_requests_are_rejected(self):
        anon_client = APIClient()
        url_get = f"/api/v1/incidents/{self.incident.id}/analysis/"
        url_post = f"/api/v1/incidents/{self.incident.id}/analyze/"

        resp_get = anon_client.get(url_get)
        self.assertEqual(resp_get.status_code, 401)
        self.assertFalse(resp_get.json()["success"])
        self.assertEqual(resp_get.json()["error"]["code"], "NOT_AUTHENTICATED")

        resp_post = anon_client.post(url_post)
        self.assertEqual(resp_post.status_code, 401)
        self.assertFalse(resp_post.json()["success"])
        self.assertEqual(resp_post.json()["error"]["code"], "NOT_AUTHENTICATED")

    def test_get_analysis_unknown_or_foreign_incident_returns_404(self):
        # Unknown ID
        res = self.client.get("/api/v1/incidents/999999/analysis/")
        self.assertEqual(res.status_code, 404)

        # Foreign org incident
        res = self.client.get(
            f"/api/v1/incidents/{self.other_incident.id}/analysis/"
        )
        self.assertEqual(res.status_code, 404)

    def test_get_analysis_when_no_analysis_exists_returns_404(self):
        res = self.client.get(f"/api/v1/incidents/{self.incident.id}/analysis/")
        self.assertEqual(res.status_code, 404)
        self.assertFalse(res.json()["success"])
        self.assertEqual(
            res.json()["message"], "No analysis exists for this incident."
        )

    def test_get_analysis_returns_latest_analysis_payload(self):
        analysis = AIAnalysis.objects.create(
            incident=self.incident,
            status=AIAnalysis.Status.READY,
            root_cause="Null reference in map",
            suggested_fix="Check for null before calling map",
            confidence=AIAnalysis.Confidence.HIGH,
            model_used="openai/gpt-oss-20b:free",
        )

        res = self.client.get(f"/api/v1/incidents/{self.incident.id}/analysis/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        data = res.json()["data"]["analysis"]
        self.assertEqual(data["id"], str(analysis.id))
        self.assertEqual(data["incident_id"], str(self.incident.id))
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["root_cause"], "Null reference in map")
        self.assertEqual(data["suggested_fix"], "Check for null before calling map")
        self.assertEqual(data["confidence"], "high")
        self.assertEqual(data["model_used"], "openai/gpt-oss-20b:free")

    def test_get_analysis_returns_pending_status(self):
        analysis = AIAnalysis.objects.create(
            incident=self.incident,
            status=AIAnalysis.Status.PENDING,
        )

        res = self.client.get(f"/api/v1/incidents/{self.incident.id}/analysis/")
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]["analysis"]
        self.assertEqual(data["id"], str(analysis.id))
        self.assertEqual(data["status"], "pending")

    def test_post_analyze_unknown_or_foreign_incident_returns_404(self):
        res = self.client.post("/api/v1/incidents/999999/analyze/")
        self.assertEqual(res.status_code, 404)

        res = self.client.post(
            f"/api/v1/incidents/{self.other_incident.id}/analyze/"
        )
        self.assertEqual(res.status_code, 404)

    @patch("apps.ai.tasks.analyze_incident.delay")
    def test_post_analyze_enqueues_celery_task_and_creates_pending_analysis(
        self, mock_delay
    ):
        res = self.client.post(f"/api/v1/incidents/{self.incident.id}/analyze/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        data = res.json()["data"]["analysis"]
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["incident_id"], str(self.incident.id))

        mock_delay.assert_called_once_with(str(self.incident.id))
        self.assertTrue(
            AIAnalysis.objects.filter(
                incident=self.incident, status=AIAnalysis.Status.PENDING
            ).exists()
        )

    @patch("apps.ai.tasks.analyze_incident.delay")
    def test_post_analyze_bypasses_cache_window(self, mock_delay):
        # Create a fresh ready analysis (0 minutes old, well inside 6-hour cache)
        AIAnalysis.objects.create(
            incident=self.incident,
            status=AIAnalysis.Status.READY,
            root_cause="Old cause",
            suggested_fix="Old fix",
            confidence=AIAnalysis.Confidence.MEDIUM,
        )

        res = self.client.post(f"/api/v1/incidents/{self.incident.id}/analyze/")
        self.assertEqual(res.status_code, 200)
        mock_delay.assert_called_once_with(str(self.incident.id))

        # There are now two analyses: the old READY one and a new PENDING one
        self.assertEqual(
            AIAnalysis.objects.filter(incident=self.incident).count(), 2
        )
        latest = AIAnalysis.objects.filter(incident=self.incident).first()
        self.assertEqual(latest.status, AIAnalysis.Status.PENDING)
