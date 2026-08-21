"""Tests for bulk incident operations (bulk-update, bulk-resolve, bulk-assign)."""

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from uuid import uuid4

from apps.events.tests.test_events import create_project, register_and_login
from apps.incidents.models import Incident, TimelineEntry
from apps.incidents.tests.test_incidents import IncidentSetupMixin


class BulkIncidentOperationsTests(IncidentSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.add_event(message="Error 1", level="error")
        self.add_event(message="Error 2", level="error")
        self.add_event(message="Error 3", level="error")

        incidents_data = self.list_incidents().data["data"]["incidents"]
        self.incident_ids = [i["id"] for i in incidents_data]
        self.assertEqual(len(self.incident_ids), 3)

    def test_bulk_resolve_endpoint(self):
        target_ids = self.incident_ids[:2]
        response = self.client.post(
            "/api/v1/incidents/bulk-resolve/",
            {"incident_ids": target_ids},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["updated_count"], 2)

        # Check DB status
        resolved_count = Incident.objects.filter(
            id__in=target_ids, status=Incident.Status.RESOLVED
        ).count()
        self.assertEqual(resolved_count, 2)

        # Timeline entry created
        for inc_id in target_ids:
            entry = TimelineEntry.objects.filter(
                incident_id=inc_id, kind=TimelineEntry.Kind.STATUS_CHANGE
            ).first()
            self.assertIsNotNone(entry)

    def test_bulk_update_endpoint_status_and_severity(self):
        target_ids = self.incident_ids
        response = self.client.post(
            "/api/v1/incidents/bulk-update/",
            {
                "incident_ids": target_ids,
                "status": "investigating",
                "severity": "critical",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["updated_count"], 3)

        for inc in Incident.objects.filter(id__in=target_ids):
            self.assertEqual(inc.status, Incident.Status.INVESTIGATING)
            self.assertEqual(inc.severity, Incident.Severity.CRITICAL)

    def test_bulk_assign_endpoint(self):
        # Register second user in org
        target_ids = self.incident_ids[:2]

        response = self.client.post(
            "/api/v1/incidents/bulk-assign/",
            {"incident_ids": target_ids, "assigned_to": None},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["updated_count"], 2)

    def test_bulk_update_validation_empty_ids(self):
        response = self.client.post(
            "/api/v1/incidents/bulk-update/",
            {"incident_ids": []},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_bulk_update_tenant_isolation(self):
        bob = APIClient()
        register_and_login(bob, "bob@example.io")
        bob_project = create_project(bob, name="BobApp")

        # Bob attempts to update Alice's incidents
        response = bob.post(
            "/api/v1/incidents/bulk-update/",
            {"incident_ids": self.incident_ids, "status": "resolved"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        # 0 updated because Bob has no access to Alice's incidents
        self.assertEqual(response.data["data"]["updated_count"], 0)
