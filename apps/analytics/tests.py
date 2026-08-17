"""Phase 3C: dashboard overview + stats endpoints.

DoD verification:
- overview loads real aggregates (open counts, event trend, top errors),
- stats returns correct bucket shapes for 24h/7d/30d,
- everything is membership-scoped and optionally narrowed by project.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.analytics.cache import build_dashboard_key, bump_project_dashboard_version
from apps.events.models import ErrorGroup, Event
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
        # Phase 5C: caches persist across tests in the shared LocMemCache; clear
        # so each test starts from a cold, deterministic slate.
        cache.clear()

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

    def test_overview_served_from_cache_and_invalidated_on_event(self):
        # Patch the selector (imported into views) so we can count cold
        # computes and assert the cached value is what gets returned.
        cached_shape = {
            "open_incidents": {
                "total": 0,
                "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            },
            "events_24h": 7,
            "event_trend": {"percent_change": 0, "trend": "flat"},
            "resolved_24h": 0,
            "top_errors": [],
            "health": "healthy",
        }
        with patch(
            "apps.analytics.views.overview_for_user", return_value=cached_shape
        ) as mock_overview:
            res1 = self.client.get("/api/v1/dashboard/overview/")
            self.assertEqual(res1.status_code, 200)
            self.assertEqual(res1.data["data"]["overview"]["events_24h"], 7)
            # Identical repeat within the TTL is served from cache.
            self.client.get("/api/v1/dashboard/overview/")
            self.assertEqual(mock_overview.call_count, 1)
            # A new event write bumps the project version -> cache invalidated.
            bump_project_dashboard_version(self.project.id)
            self.client.get("/api/v1/dashboard/overview/")
            self.assertEqual(mock_overview.call_count, 2)

    def test_stats_served_from_cache_and_invalidated_on_event(self):
        canned_points = [
            {"ts": "2026-01-01T00:00:00+00:00", "events": 3, "incidents": 1}
        ]
        with patch(
            "apps.analytics.views.stats_for_user", return_value=canned_points
        ) as mock_stats:
            res1 = self.client.get("/api/v1/dashboard/stats/?range=24h")
            self.assertEqual(res1.status_code, 200)
            self.assertEqual(res1.data["data"]["stats"]["points"], canned_points)
            # Repeated 24h request hits the cache.
            self.client.get("/api/v1/dashboard/stats/?range=24h")
            self.assertEqual(mock_stats.call_count, 1)
            # A different range is a distinct key -> recomputed.
            self.client.get("/api/v1/dashboard/stats/?range=7d")
            self.assertEqual(mock_stats.call_count, 2)
            # A new event write invalidates the 24h key.
            bump_project_dashboard_version(self.project.id)
            self.client.get("/api/v1/dashboard/stats/?range=24h")
            self.assertEqual(mock_stats.call_count, 3)

    def test_version_bump_changes_cache_key(self):
        before = build_dashboard_key("overview", [self.project.id], None)
        bump_project_dashboard_version(self.project.id)
        after = build_dashboard_key("overview", [self.project.id], None)
        self.assertNotEqual(before, after)
        bump_project_dashboard_version(self.project.id)
        self.assertNotEqual(
            after, build_dashboard_key("overview", [self.project.id], None)
        )

    def _make_event(self, project, message):
        from django.utils import timezone

        group = ErrorGroup.objects.create(
            project=project,
            fingerprint="fp-%s" % message,
            title=message,
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        return Event.objects.create(
            project=project,
            error_group=group,
            message=message,
            fingerprint="fp-%s" % message,
        )

    def test_event_write_invalidates_dashboard_cache_key(self):
        # The post_save signal on Event must bump the project version so the
        # next dashboard read recomputes (DoD: fresh within a couple seconds).
        # We create the Event directly (the signal fires) to avoid the
        # broker-dependent ingest pipeline in this offline test environment.
        key_before = build_dashboard_key("overview", [self.project.id], None)
        self._make_event(self.project, "VersionedError")
        key_after = build_dashboard_key("overview", [self.project.id], None)
        self.assertNotEqual(key_before, key_after)


class ServicesHealthTestCase(TestCase):
    """Phase 3C: the per-service health catalog endpoint."""

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
        cache.clear()

    def _ingest(self, project, message, level="error", **kwargs):
        return ingest_event(
            project,
            message=message,
            level=level,
            service=kwargs.pop("service", "payment-api"),
            environment=kwargs.pop("environment", "production"),
            **kwargs,
        )

    def test_catalog_aggregates_volume_rate_status_and_environments(self):
        self._ingest(self.project, "FatalError: boom", level="fatal")
        self._ingest(self.project, "TimeoutError: gateway", level="error")
        self._ingest(self.project, "Warn: slow", level="warning")
        self._ingest(
            self.project,
            "TimeoutError: staging",
            level="error",
            environment="staging",
        )

        res = self.client.get("/api/v1/services/health/")
        self.assertEqual(res.status_code, 200)
        catalog = res.json()["data"]["catalog"]
        self.assertEqual(catalog["range"], "24h")
        self.assertEqual(len(catalog["services"]), 1)
        service = catalog["services"][0]
        self.assertEqual(service["name"], "payment-api")
        self.assertEqual(service["events"], 4)
        self.assertEqual(service["error_events"], 3)
        self.assertEqual(service["fatal_events"], 1)
        self.assertEqual(service["status"], "critical")
        self.assertEqual(service["error_rate"], 0.75)
        self.assertEqual(service["error_groups"], 4)
        self.assertEqual(len(service["environments"]), 2)
        by_env = {env["name"]: env for env in service["environments"]}
        self.assertEqual(by_env["production"]["events"], 3)
        self.assertEqual(by_env["production"]["error_events"], 2)
        self.assertEqual(by_env["staging"]["events"], 1)
        self.assertEqual(by_env["staging"]["error_events"], 1)
        self.assertEqual(catalog["summary"]["total_services"], 1)
        self.assertEqual(catalog["summary"]["critical_services"], 1)
        self.assertEqual(catalog["summary"]["avg_error_rate"], 0.75)

    def test_status_ladder_and_ordering(self):
        self._ingest(self.project, "H1", level="warning", service="healthy-svc")
        self._ingest(self.project, "H2", level="error", service="degraded-svc")
        self._ingest(
            self.project, "H3", level="fatal", service="critical-svc"
        )

        res = self.client.get("/api/v1/services/health/")
        names = [s["name"] for s in res.json()["data"]["catalog"]["services"]]
        self.assertEqual(
            names, ["critical-svc", "degraded-svc", "healthy-svc"]
        )
        statuses = {
            s["name"]: s["status"]
            for s in res.json()["data"]["catalog"]["services"]
        }
        self.assertEqual(statuses["critical-svc"], "critical")
        self.assertEqual(statuses["degraded-svc"], "degraded")
        self.assertEqual(statuses["healthy-svc"], "healthy")

    def test_uptime_counts_only_hours_with_traffic(self):
        now = timezone.now()
        self._ingest(self.project, "Old", level="fatal")
        Event.objects.filter(
            message="Old", project=self.project
        ).update(created_at=now - timedelta(hours=26))
        for hour in range(3):
            event = self._ingest(
                self.project, f"Fatal{hour}", level="fatal"
            )
            Event.objects.filter(
                message=f"Fatal{hour}", project=self.project
            ).update(created_at=now - timedelta(hours=hour))

        res = self.client.get("/api/v1/services/health/")
        service = res.json()["data"]["catalog"]["services"][0]
        self.assertEqual(service["events"], 3)
        self.assertEqual(service["uptime"], 0)

    def test_uptime_is_100_when_no_fatal_events(self):
        self._ingest(self.project, "Warn: slow", level="warning")
        res = self.client.get("/api/v1/services/health/")
        service = res.json()["data"]["catalog"]["services"][0]
        self.assertEqual(service["uptime"], 100)
        self.assertEqual(service["status"], "healthy")

    def test_unattributed_events_are_excluded(self):
        self._ingest(self.project, "NoService", level="error", service="")
        res = self.client.get("/api/v1/services/health/")
        catalog = res.json()["data"]["catalog"]
        self.assertEqual(catalog["summary"]["total_services"], 0)
        self.assertEqual(catalog["services"], [])

    def test_range_parameter_changes_window(self):
        self._ingest(self.project, "Fresh", level="error")
        stale = self._ingest(self.project, "Stale", level="error")
        Event.objects.filter(message="Stale").update(
            created_at=timezone.now() - timedelta(days=5)
        )
        cache.clear()

        res = self.client.get("/api/v1/services/health/?range=7d")
        self.assertEqual(res.status_code, 200)
        service = res.json()["data"]["catalog"]["services"][0]
        self.assertEqual(service["events"], 2)

        res = self.client.get("/api/v1/services/health/?range=30d")
        self.assertEqual(res.status_code, 200)
        service = res.json()["data"]["catalog"]["services"][0]
        self.assertEqual(service["events"], 2)

    def test_membership_scoping_and_project_narrowing(self):
        outsider = User.objects.create_user(
            email="outsider@example.com",
            password=PASSWORD,
            email_verified=True,
        )
        self._ingest(self.project, "Mine", level="error")
        self._ingest(self.other_project, "AlsoMine", level="error")

        res = self.client.get("/api/v1/services/health/")
        names = [s["name"] for s in res.json()["data"]["catalog"]["services"]]
        self.assertEqual(len(names), 1)

        res = self.client.get(
            "/api/v1/services/health/?project_id=%s" % self.project.id
        )
        names = [s["name"] for s in res.json()["data"]["catalog"]["services"]]
        self.assertEqual(len(names), 1)

        res = self.client.get(
            "/api/v1/services/health/?project_id=%s" % self.other_project.id
        )
        names = [s["name"] for s in res.json()["data"]["catalog"]["services"]]
        self.assertEqual(len(names), 1)

        foreign = Project.objects.create(
            organization=Organization.objects.create(
                name="Foreign", owner=outsider
            ),
            name="Their",
        )
        Membership.objects.create(
            user=outsider, organization=foreign.organization,
            role=MembershipRole.OWNER,
        )
        self._ingest(foreign, "Theirs", level="error", service="their-svc")

        res = self.client.get(
            "/api/v1/services/health/?project_id=%s" % foreign.id
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.json()["data"]["catalog"]["summary"]["total_services"], 0
        )

    def test_invalid_range_rejected(self):
        res = self.client.get("/api/v1/services/health/?range=year")
        self.assertEqual(res.status_code, 400)
        self.assertIn("range", res.json()["error"]["fields"])

    def test_catalog_cache_is_version_bumped_by_writes(self):
        with patch("apps.analytics.views.services_health_for_user") as mock:
            mock.return_value = {"services": [], "summary": {}}
            self.client.get("/api/v1/services/health/")
            self.client.get("/api/v1/services/health/")
            self.assertEqual(mock.call_count, 1)
            self._make_event(self.project, "Invalidator")
            self.client.get("/api/v1/services/health/")
            self.assertEqual(mock.call_count, 2)

    def _make_event(self, project, message):
        group = ErrorGroup.objects.create(
            project=project,
            fingerprint="fp-svc-%s" % message,
            title=message,
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        return Event.objects.create(
            project=project,
            error_group=group,
            message=message,
            fingerprint="fp-svc-%s" % message,
            service="payment-api",
        )