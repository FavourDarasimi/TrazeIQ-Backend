"""Incident ?search= — portable substring search across title/message/service/endpoint."""

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.events.tests.test_events import create_project, register_and_login


class IncidentSearchSetupMixin:
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        register_and_login(self.client, "dev@trazeiq.io")
        self.project = create_project(self.client)
        self.ingest_client = APIClient()

    def add_event(self, *, message="KeyError: boom", service="web", endpoint="/api/pay"):
        return self.ingest_client.post(
            "/api/v1/events/",
            {
                "message": message,
                "stacktrace": "Traceback (most recent call last):\n  File /app/web.py:40 in handler",
                "level": "error",
                "environment": "production",
                "service": service,
                "endpoint": endpoint,
            },
            format="json",
            headers={"X-API-Key": self.project["api_key"]},
        )

    def search(self, **params):
        return self.client.get("/api/v1/incidents/", params)


class IncidentSearchTests(IncidentSearchSetupMixin, TestCase):
    def test_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.get("/api/v1/incidents/", {"search": "boom"})
        self.assertEqual(response.status_code, 401)

    def test_matches_title_case_insensitively(self):
        self.add_event(message="DatabaseError: connection refused")
        response = self.search(search="databaseerror")
        self.assertEqual(response.status_code, 200)
        incidents = response.data["data"]["incidents"]
        self.assertEqual(len(incidents), 1)
        self.assertIn("DatabaseError", incidents[0]["error_group"]["title"])

    def test_matches_service_and_endpoint(self):
        self.add_event(message="boom one", service="checkout-api", endpoint="/api/pay")
        self.add_event(message="boom two", service="web", endpoint="/v1/orders")

        by_service = self.search(search="checkout").data["data"]["incidents"]
        self.assertEqual(len(by_service), 1)
        self.assertIn("boom one", by_service[0]["error_group"]["title"])

        by_endpoint = self.search(search="v1/orders").data["data"]["incidents"]
        self.assertEqual(len(by_endpoint), 1)
        self.assertIn("boom two", by_endpoint[0]["error_group"]["title"])

    def test_no_match_returns_empty_list(self):
        self.add_event(message="KeyError: boom")
        response = self.search(search="definitely-not-here-xyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["incidents"], [])

    def test_blank_search_is_noop(self):
        self.add_event(message="KeyError: boom")
        response = self.search(search="   ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]["incidents"]), 1)

    def test_search_combines_with_status_and_severity_filters(self):
        self.add_event(message="fatal payment boom")
        # error level -> high severity; flip one incident to resolved via ORM
        from apps.incidents.models import Incident

        Incident.objects.update(status=Incident.Status.RESOLVED)
        resolved = self.search(search="payment", status="resolved")
        self.assertEqual(len(resolved.data["data"]["incidents"]), 1)
        open_only = self.search(search="payment", status="open")
        self.assertEqual(open_only.data["data"]["incidents"], [])

    def test_search_does_not_duplicate_incidents_with_many_events(self):
        # Same fingerprint -> one incident with count=2; search must return it once.
        self.add_event(message="KeyError: boom", service="web")
        self.add_event(message="KeyError: boom", service="web")
        response = self.search(search="boom")
        incidents = response.data["data"]["incidents"]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["error_group"]["count"], 2)


class IncidentSearchTenantIsolationTests(TestCase):
    """Agent.md rule 2: search never leaks across organizations."""

    def setUp(self):
        cache.clear()
        self.alice = APIClient()
        register_and_login(self.alice, "alice@trazeiq.io")
        alice_project = create_project(self.alice)
        self.alice.post(
            "/api/v1/events/",
            {"message": "alice unique boom xyz", "level": "error"},
            format="json",
            headers={"X-API-Key": alice_project["api_key"]},
        )

        self.bob = APIClient()
        register_and_login(self.bob, "bob@example.io")
        bob_project = create_project(self.bob, name="BobApp")
        self.bob.post(
            "/api/v1/events/",
            {"message": "bob unique boom xyz", "level": "error"},
            format="json",
            headers={"X-API-Key": bob_project["api_key"]},
        )

    def test_cannot_search_another_orgs_incidents(self):
        # Bob searches a term that only exists in Alice's org.
        response = self.bob.get("/api/v1/incidents/", {"search": "alice unique"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["incidents"], [])

    def test_search_scoped_to_own_org(self):
        response = self.bob.get("/api/v1/incidents/", {"search": "bob unique"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]["incidents"]), 1)
