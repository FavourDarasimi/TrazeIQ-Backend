"""Phase 3C: dashboard overview + stats endpoints.

DoD verification:
- overview loads real aggregates (open counts, event trend, top errors),
- stats returns correct bucket shapes for 24h/7d/30d,
- everything is membership-scoped and optionally narrowed by project.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.events.services import ingest_event
from apps.incidents.models import Incident
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.projects.models import Project

PASSWORD = "Password123!"


class DashboardAnalyticsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com", password=PASSWORD, email_verified=True
        )
        self.org = Organization.objects.create(name="Acme Inc", owner=self.user)
        Membership.objects.create(
            user=self.user, organization=self.org, role=MembershipRole.OWNER
        )
        self.project = Project.objects.create(organization=self.org, name="API")
        self.other_project = Project.objects.create(
            organization=self.org, name="Webhook"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _ingest(self, project, message, level="error"):
        return ingest_event(project, message=message, level=level)

    def _open_incident(self, event, severity, **overrides):
        incident = Incident.objects.get(error_group=event.error_group)
        incident.severity = severity
        for key, value in overrides.items():
            setattr(incident, key, value)
        incident.save()
        return incident

    def test_overview_reports_counts_breakdown_trend_and_health(self):
        critical = self._ingest(
            self.project, "DatabaseError: connection refused", level="fatal"
        )
        high = self._ingest(self.project, "TimeoutError: gateway", level="error")
        low = self._ingest(self.project, "DeprecationWarning: soon", level="warning")
        self._ingest(self.other_project, "OtherError", level="error")

        # ingest_event already opened an incident per group; adjust severities
        # and resolve one to shape the aggregates.
        critical_incident = Incident.objects.get(error_group=critical.error_group)
        critical_incident.severity = Incident.Severity.CRITICAL
        critical_incident.save()
        high_incident = Incident.objects.get(error_group=high.error_group)
        high_incident.severity = Incident.Severity.HIGH
        high_incident.save()
        low_incident = Incident.objects.get(error_group=low.error_group)
        low_incident.status = Incident.Status.RESOLVED
        low_incident.resolved_at = timezone.now()
        low_incident.save()

        res = self.client.get("/api/v1/dashboard/overview/")
        self.assertEqual(res.status_code, 200)
        overview = res.data["data"]["overview"]

        self.assertEqual(overview["open_incidents"]["total"], 3)
        self.assertEqual(
            overview["open_incidents"]["by_severity"],
            {"critical": 1, "high": 2, "medium": 0, "low": 0},
        )
        self.assertEqual(overview["events_24h"], 4)
        self.assertEqual(overview["resolved_24h"], 1)
        self.assertEqual(overview["health"], "critical")
        self.assertEqual(overview["event_trend"]["trend"], "up")

        titles = {error["title"] for error in overview["top_errors"]}
        self.assertIn("DatabaseError: connection refused", titles)
        critical_top = next(
            error
            for error in overview["top_errors"]
            if error["title"] == "DatabaseError: connection refused"
        )
        self.assertIsNotNone(critical_top["incident_id"])
        self.assertEqual(critical_top["severity"], "critical")
        self.assertEqual(critical_top["count"], 1)

    def test_overview_scopes_to_project(self):
        self._ingest(self.project, "ProjectError", level="error")
        self._ingest(self.other_project, "OtherError", level="error")

        res = self.client.get(
            "/api/v1/dashboard/overview/?project_id=%s" % self.other_project.id
        )
        overview = res.data["data"]["overview"]
        self.assertEqual(overview["events_24h"], 1)
        self.assertEqual(overview["top_errors"][0]["title"], "OtherError")

    def test_stats_bucket_shapes_and_events(self):
        self._ingest(self.project, "A", level="error")
        self._ingest(self.project, "B", level="error")

        res = self.client.get("/api/v1/dashboard/stats/?range=24h")
        self.assertEqual(res.status_code, 200)
        stats = res.data["data"]["stats"]
        self.assertEqual(stats["range"], "24h")
        self.assertLessEqual(len(stats["points"]), 25)  # 24 full + current partial hour
        self.assertGreaterEqual(len(stats["points"]), 24)
        self.assertEqual(
            sum(point["events"] for point in stats["points"]), 2,
        )

        for param, expected_len in (("7d", 7), ("30d", 30)):
            res = self.client.get(f"/api/v1/dashboard/stats/?range={param}")
            self.assertEqual(res.status_code, 200, param)
            points = res.data["data"]["stats"]["points"]
            self.assertLessEqual(len(points), expected_len + 1)
            self.assertGreaterEqual(len(points), expected_len)

    def test_invalid_range_is_rejected(self):
        res = self.client.get("/api/v1/dashboard/stats/?range=year")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.data["success"])

    def test_foreign_project_narrows_to_empty(self):
        stranger = User.objects.create_user(
            email="stranger@example.com", password=PASSWORD, email_verified=True
        )
        foreign_org = Organization.objects.create(name="Other", owner=stranger)
        foreign = Project.objects.create(organization=foreign_org, name="private")
        self._ingest(foreign, "SecretError", level="error")

        res = self.client.get(
            "/api/v1/dashboard/overview/?project_id=%s" % foreign.id
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["data"]["overview"]["events_24h"], 0)

        res = self.client.get(
            "/api/v1/dashboard/stats/?project_id=%s&range=24h" % foreign.id
        )
        points = res.data["data"]["stats"]["points"]
        self.assertFalse(any(point["events"] for point in points))