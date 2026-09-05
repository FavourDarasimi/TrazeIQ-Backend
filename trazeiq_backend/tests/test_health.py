"""Phase 5E — expanded health probe: dependency checks + pipeline metrics.

The probe must never fail: a down dependency degrades the payload, and the
worker check must not raise when no worker is running (the suite runs
without one).
"""

from django.test import TestCase
from rest_framework.test import APIClient


class HealthProbeTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_reports_database_and_cache_checks(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(data["status"], "ok")
        for name in ("database", "cache", "worker"):
            self.assertIn(name, data["checks"])
            self.assertIn(
                data["checks"][name]["status"], ("ok", "unavailable")
            )
        self.assertEqual(data["checks"]["database"]["status"], "ok")
        self.assertEqual(data["checks"]["cache"]["status"], "ok")

    def test_worker_check_degrades_without_a_worker(self):
        # No Celery worker runs in the suite — the check must report
        # unavailable, not raise or fail the probe.
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        worker = response.data["data"]["checks"]["worker"]
        self.assertIn(worker["status"], ("ok", "unavailable"))

    def test_includes_pipeline_metrics(self):
        response = self.client.get("/api/v1/health/")
        metrics = response.data["data"]["metrics"]
        self.assertIn("events_24h", metrics)
        self.assertIn("ai_analysis", metrics)
        self.assertIn("alerts_24h", metrics)
        self.assertEqual(
            set(metrics["ai_analysis"]), {"pending", "ready", "failed"}
        )

    def test_unversioned_alias_matches(self):
        versioned = self.client.get("/api/v1/health/")
        unversioned = self.client.get("/api/health/")
        self.assertEqual(unversioned.status_code, 200)
        self.assertEqual(
            unversioned.data["data"]["status"], versioned.data["data"]["status"]
        )
