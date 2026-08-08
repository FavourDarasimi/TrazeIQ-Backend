"""Phase 1F: incident read endpoints — list/detail/timeline, org-scoped.

The 1F frontend dashboard renders from these; the DoD is that real backend
data flows through list/detail/timeline, filters work, and tenant isolation
(Agent.md rule 2) holds for every one of the new querysets.
"""

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from uuid import uuid4

from apps.events.tests.test_events import create_project, register_and_login
from apps.incidents.models import Incident


class IncidentSetupMixin:
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        register_and_login(self.client, "dev@trazeiq.io")
        self.project = create_project(self.client)
        self.ingest_client = APIClient()

    def add_event(self, *, message="KeyError: boom", level="error"):
        return self.ingest_client.post(
            "/api/v1/events/",
            {
                "message": message,
                "stacktrace": "Traceback (most recent call last):\n  File /app/web.py:40 in handler\n  raise KeyError('boom')",
                "level": level,
                "environment": "production",
                "service": "web",
            },
            format="json",
            headers={"X-API-Key": self.project["api_key"]},
        )

    def list_incidents(self, **params):
        return self.client.get("/api/v1/incidents/", params)


class IncidentListTests(IncidentSetupMixin, TestCase):
    def test_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.get("/api/v1/incidents/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "NOT_AUTHENTICATED")

    def test_list_returns_group_summary_and_latest_event(self):
        self.add_event(message="KeyError: boom", level="error")
        response = self.list_incidents()
        self.assertEqual(response.status_code, 200)
        incident = response.data["data"]["incidents"][0]
        self.assertEqual(incident["severity"], "high")  # error -> high
        self.assertEqual(incident["status"], "open")
        self.assertEqual(incident["error_group"]["title"], "KeyError: boom")
        self.assertEqual(incident["error_group"]["count"], 1)
        self.assertEqual(incident["latest_event"]["message"], "KeyError: boom")
        self.assertIn("Traceback", incident["latest_event"]["stacktrace"])
        self.assertEqual(incident["project"]["name"], "Web")
        self.assertEqual(incident["project"]["environment"], "production")
        self.assertIsNotNone(incident["latest_event"]["created_at"])

    def test_repeated_occurrences_bump_count_and_refresh_last_seen(self):
        first = self.add_event(message="KeyError: boom").json()
        second = self.add_event(message="KeyError: boom").json()
        incident = self.list_incidents().data["data"]["incidents"][0]
        self.assertEqual(incident["error_group"]["count"], 2)
        # first_seen/last_seen are taken just before the insert, so bound
        # them against the persisted occurrence timestamps rather than
        # asserting equality (µs drift by design).
        self.assertLessEqual(
            incident["error_group"]["first_seen"],
            first["data"]["event"]["created_at"],
        )
        self.assertLessEqual(
            incident["error_group"]["last_seen"],
            second["data"]["event"]["created_at"],
        )
        self.assertLess(
            incident["error_group"]["first_seen"],
            incident["error_group"]["last_seen"],
        )

    def test_filters_by_severity(self):
        self.add_event(message="fatal boom", level="fatal")  # critical
        self.add_event(message="warn boom", level="warning")  # medium
        response = self.list_incidents(severity="critical")
        titles = [i["error_group"]["title"] for i in response.data["data"]["incidents"]]
        self.assertEqual(titles, ["fatal boom"])

    def test_filters_by_status(self):
        self.add_event(message="boom")
        Incident.objects.update(status=Incident.Status.INVESTIGATING)
        response = self.list_incidents(status="investigating")
        self.assertEqual(len(response.data["data"]["incidents"]), 1)
        response = self.list_incidents(status="resolved")
        self.assertEqual(response.data["data"]["incidents"], [])

    def test_filters_by_project(self):
        second = create_project(self.client, name="API")
        self.add_event(message="web boom")
        self.ingest_client.post(
            "/api/v1/events/",
            {"message": "api boom", "level": "error"},
            format="json",
            headers={"X-API-Key": second["api_key"]},
        )
        response = self.list_incidents(project=second["id"])
        titles = [i["error_group"]["title"] for i in response.data["data"]["incidents"]]
        self.assertEqual(titles, ["api boom"])

    def test_invalid_status_filter_is_400(self):
        response = self.list_incidents(status="bogus")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")
        self.assertIn("status", response.data["error"]["fields"])

    def test_invalid_project_filter_is_400(self):
        response = self.list_incidents(project="not-a-number")
        self.assertEqual(response.status_code, 400)


class IncidentDetailTests(IncidentSetupMixin, TestCase):
    def test_detail_includes_stacktrace_and_counts(self):
        self.add_event(message="KeyError: boom")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]
        response = self.client.get(f"/api/v1/incidents/{incident_id}/")
        self.assertEqual(response.status_code, 200)
        incident = response.data["data"]["incident"]
        self.assertIn("Traceback", incident["latest_event"]["stacktrace"])
        self.assertEqual(incident["error_group"]["count"], 1)
        self.assertIsNotNone(incident["error_group"]["first_seen"])
        self.assertIsNotNone(incident["error_group"]["last_seen"])

    def test_unknown_id_is_404(self):
        response = self.client.get(f"/api/v1/incidents/{uuid4()}/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")


class IncidentTimelineTests(IncidentSetupMixin, TestCase):
    def test_timeline_orders_occurrences_oldest_first(self):
        first = self.add_event(message="boom 1")
        second = self.add_event(message="boom 1")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]
        response = self.client.get(f"/api/v1/incidents/{incident_id}/timeline/")
        self.assertEqual(response.status_code, 200)
        entries = response.data["data"]["entries"]
        self.assertEqual(len(entries), 2)
        # Oldest first by created_at (UUID ids are random since the UUID
        # migration — never a meaningful sort key for this assertion).
        created_at = [e["created_at"] for e in entries]
        self.assertEqual(created_at, sorted(created_at))
        self.assertTrue(all(e["kind"] == "event" for e in entries))
        self.assertEqual(entries[0]["message"], "boom 1")
        self.assertLess(entries[0]["created_at"], entries[1]["created_at"])


class IncidentTenantIsolationTests(TestCase):
    """Agent.md rule 2: incidents are invisible across organizations."""

    def setUp(self):
        cache.clear()
        self.alice = APIClient()
        register_and_login(self.alice, "alice@trazeiq.io")
        alice_project = create_project(self.alice)
        self.alice.post(
            "/api/v1/events/",
            {"message": "alice boom", "level": "fatal"},
            format="json",
            headers={"X-API-Key": alice_project["api_key"]},
        )
        self.alice_incident_id = self.alice.get(
            "/api/v1/incidents/"
        ).data["data"]["incidents"][0]["id"]

        self.bob = APIClient()
        register_and_login(self.bob, "bob@example.io")
        self.bob_project = create_project(self.bob, name="BobApp")
        self.bob.post(
            "/api/v1/events/",
            {"message": "bob boom", "level": "error"},
            format="json",
            headers={"X-API-Key": self.bob_project["api_key"]},
        )

    def test_cannot_list_another_orgs_incidents(self):
        response = self.bob.get("/api/v1/incidents/")
        self.assertEqual(response.status_code, 200)
        ids = [i["id"] for i in response.data["data"]["incidents"]]
        self.assertNotIn(self.alice_incident_id, ids)
        self.assertEqual(len(ids), 1)  # only bob's own incident

    def test_cross_org_detail_is_404(self):
        response = self.bob.get(
            f"/api/v1/incidents/{self.alice_incident_id}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_cross_org_timeline_is_404(self):
        response = self.bob.get(
            f"/api/v1/incidents/{self.alice_incident_id}/timeline/"
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_still_sees_own_incident(self):
        response = self.alice.get(
            f"/api/v1/incidents/{self.alice_incident_id}/"
        )
        self.assertEqual(response.status_code, 200)