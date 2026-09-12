"""Phase 5E — expanded health probe: dependency checks + pipeline metrics.

The probe must never fail: a down dependency degrades the payload. No
worker exists anymore (analysis and alerts run inline), so the probe only
checks database and cache.
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
        for name in ("database", "cache"):
            self.assertIn(name, data["checks"])
            self.assertIn(
                data["checks"][name]["status"], ("ok", "unavailable")
            )
        self.assertEqual(data["checks"]["database"]["status"], "ok")
        self.assertEqual(data["checks"]["cache"]["status"], "ok")
        self.assertNotIn("worker", data["checks"])

    def test_includes_pipeline_metrics(self):
        response = self.client.get("/api/v1/health/")
        metrics = response.data["data"]["metrics"]
        self.assertIn("events_24h", metrics)
        self.assertIn("alerts_24h", metrics)

    def test_unversioned_alias_matches(self):
        versioned = self.client.get("/api/v1/health/")
        unversioned = self.client.get("/api/health/")
        self.assertEqual(unversioned.status_code, 200)
        self.assertEqual(
            unversioned.data["data"]["status"], versioned.data["data"]["status"]
        )
