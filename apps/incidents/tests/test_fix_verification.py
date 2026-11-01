from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Organization, Membership
from apps.projects.models import Project
from apps.events.models import Event, ErrorGroup
from apps.events.services import ingest_event
from apps.incidents.models import Incident, TimelineEntry


class FixVerificationAndAutoReopenTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="tester@trazeiq.io", password="StrongPassword123!"
        )
        self.org = Organization.objects.create(name="Acme Corp", owner=self.user)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role="owner",
        )
        self.project = Project.objects.create(
            organization=self.org,
            name="Checkout App",
            api_key_hash="hash-chk",
            api_key_prefix="tr_chk_",
            environment="production",
        )
        # Ingest initial event so fingerprint & ErrorGroup match exactly
        ingest_event(self.project, message="ConnectionTimeout: db replica", release="v1.4.0")
        self.incident = Incident.objects.get(project=self.project)
        self.group = self.incident.error_group

    def test_verify_fix_endpoint_sets_fix_verified(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            f"/api/v1/incidents/{self.incident.pk}/verify-fix/",
            data={
                "release": "v1.4.3",
                "commit_sha": "c0ffee123",
                "notes": "pool size bumped to 50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]["incident"]
        self.assertEqual(data["status"], "fix_verified")

        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.FIX_VERIFIED)

        timeline = TimelineEntry.objects.filter(incident=self.incident)
        self.assertTrue(timeline.exists())
        self.assertIn("Fix verified in release v1.4.3", timeline.first().content)
        self.assertIn("c0ffee123", timeline.first().content)
        self.assertIn("pool size bumped to 50", timeline.first().content)

    def test_verify_fix_with_auto_resolve(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            f"/api/v1/incidents/{self.incident.pk}/verify-fix/",
            data={
                "release": "v1.4.3",
                "auto_resolve": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.RESOLVED)
        self.assertIsNotNone(self.incident.resolved_at)

    def test_auto_reopen_when_error_recurs_after_fix_verified(self):
        # 1. Mark incident fix_verified
        self.incident.status = Incident.Status.FIX_VERIFIED
        self.incident.save()

        # 2. Ingest an event for this group
        event = ingest_event(
            self.project,
            message="ConnectionTimeout: db replica",
            release="v1.4.4",
            endpoint="/api/v1/pay",
        )

        # 3. Incident must automatically revert to OPEN
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.OPEN)
        self.assertIsNone(self.incident.resolved_at)

        # 4. TimelineEntry documenting recurrence
        timeline = TimelineEntry.objects.filter(
            incident=self.incident,
            content__contains="Automatically reopened",
        )
        self.assertTrue(timeline.exists())
        entry = timeline.first()
        self.assertIn("v1.4.4", entry.content)
        self.assertIn("/api/v1/pay", entry.content)

    def test_auto_reopen_when_error_recurs_after_resolved(self):
        # 1. Resolve incident
        self.incident.status = Incident.Status.RESOLVED
        self.incident.resolved_at = timezone.now()
        self.incident.save()

        # 2. Ingest new event
        ingest_event(
            self.project,
            message="ConnectionTimeout: db replica",
            release="v1.4.5",
        )

        # 3. Incident is reopened
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.OPEN)
        self.assertIsNone(self.incident.resolved_at)
