"""Phase 4B: timeline mixing + comments + automatic status_change logging.

DoD: the timeline endpoint returns entries in chronological order mixing all
four kinds (event / comment / status_change / ai_analysis); a posted comment
appears immediately with the correct actor; every status mutation (PATCH or
resolve) produces a ``status_change`` entry.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from uuid import uuid4

from apps.events.tests.test_events import create_org, create_project, register_and_login
from apps.incidents.models import Incident, TimelineEntry
from apps.organizations.models import Membership

from .test_incidents import IncidentSetupMixin

User = get_user_model()


class TimelineMixingTests(IncidentSetupMixin, TestCase):
    def test_timeline_mixes_all_four_kinds_chronologically(self):
        self.add_event(message="boom 1")
        self.add_event(message="boom 1")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]

        response = self.client.patch(
            f"/api/v1/incidents/{incident_id}/",
            {"status": "investigating"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/api/v1/incidents/{incident_id}/comments/",
            {"content": "Looking into this now"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        TimelineEntry.objects.create(
            incident_id=incident_id,
            kind=TimelineEntry.Kind.AI_ANALYSIS,
            content="Root cause: unhandled KeyError.",
        )

        response = self.client.get(f"/api/v1/incidents/{incident_id}/timeline/")
        self.assertEqual(response.status_code, 200)
        entries = response.data["data"]["entries"]
        kinds = [e["kind"] for e in entries]
        self.assertEqual(
            kinds,
            ["event", "event", "status_change", "comment", "ai_analysis"],
        )
        created_at = [e["created_at"] for e in entries]
        self.assertEqual(created_at, sorted(created_at))

    def test_event_rows_keep_occurrence_fields(self):
        self.add_event(message="boom 1", level="fatal")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]
        response = self.client.get(f"/api/v1/incidents/{incident_id}/timeline/")
        entry = response.data["data"]["entries"][0]
        self.assertEqual(entry["kind"], "event")
        self.assertEqual(entry["message"], "boom 1")
        self.assertEqual(entry["level"], "fatal")
        self.assertEqual(entry["content"], "")
        self.assertIsNone(entry["actor_email"])

    def test_timeline_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.get(f"/api/v1/incidents/{uuid4()}/timeline/")
        self.assertEqual(response.status_code, 401)

    def test_cross_org_timeline_is_404(self):
        other = APIClient()
        register_and_login(other, "bob@example.io")
        create_project(other, name="BobApp")
        self.add_event(message="alice boom")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]
        response = other.get(f"/api/v1/incidents/{incident_id}/timeline/")
        self.assertEqual(response.status_code, 404)


class CommentTests(IncidentSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.org_id = create_org(self.client, "Acme")
        self.project = create_project(self.client, org=self.org_id)
        self.ingest_client.post(
            "/api/v1/events/",
            {"message": "KeyError: boom", "level": "error"},
            format="json",
            headers={"X-API-Key": self.project["api_key"]},
        )
        self.incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]

    def comment(self, client=None, content="Looking into this"):
        return (client or self.client).post(
            f"/api/v1/incidents/{self.incident_id}/comments/",
            {"content": content},
            format="json",
        )

    def add_member(self, email, role):
        member = APIClient()
        register_and_login(member, email)
        Membership.objects.create(
            user=User.objects.get(email=email),
            organization_id=self.org_id,
            role=role,
        )
        return member

    def test_comment_appears_immediately_with_correct_actor(self):
        response = self.comment(content="  Looking into this now  ")
        self.assertEqual(response.status_code, 201)
        entry = response.data["data"]["entry"]
        self.assertEqual(entry["kind"], "comment")
        self.assertEqual(entry["content"], "Looking into this now")
        self.assertEqual(entry["actor_email"], "dev@trazeiq.io")

        timeline = self.client.get(
            f"/api/v1/incidents/{self.incident_id}/timeline/"
        ).data["data"]["entries"]
        comments = [e for e in timeline if e["kind"] == "comment"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["id"], entry["id"])
        self.assertEqual(comments[0]["content"], "Looking into this now")
        self.assertEqual(comments[0]["actor_email"], "dev@trazeiq.io")

    def test_comment_requires_content(self):
        for payload in ({}, {"content": ""}, {"content": "   "}):
            response = self.client.post(
                f"/api/v1/incidents/{self.incident_id}/comments/",
                payload,
                format="json",
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                response.data["error"]["code"], "VALIDATION_FAILED"
            )
            self.assertIn("content", response.data["error"]["fields"])

    def test_comment_content_is_capped(self):
        response = self.comment(content="x" * 5001)
        self.assertEqual(response.status_code, 400)

    def test_comment_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.post(
            f"/api/v1/incidents/{self.incident_id}/comments/",
            {"content": "hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_cross_org_comment_is_404(self):
        other = APIClient()
        register_and_login(other, "bob@example.io")
        create_project(other, name="BobApp")
        response = self.comment(client=other)
        self.assertEqual(response.status_code, 404)

    def test_viewer_cannot_comment(self):
        viewer = self.add_member("viewer@trazeiq.io", "viewer")
        response = self.comment(client=viewer)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.data["error"]["code"], "PERMISSION_DENIED"
        )
        self.assertEqual(TimelineEntry.objects.count(), 0)

    def test_developer_can_comment(self):
        developer = self.add_member("dev2@trazeiq.io", "developer")
        response = self.comment(client=developer)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data["data"]["entry"]["actor_email"], "dev2@trazeiq.io"
        )


class StatusChangeLoggingTests(IncidentSetupMixin, TestCase):
    def test_patch_status_appends_status_change_with_actor(self):
        self.add_event(message="boom")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]

        response = self.client.patch(
            f"/api/v1/incidents/{incident_id}/",
            {"status": "investigating"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        entries = self.client.get(
            f"/api/v1/incidents/{incident_id}/timeline/"
        ).data["data"]["entries"]
        status_changes = [e for e in entries if e["kind"] == "status_change"]
        self.assertEqual(len(status_changes), 1)
        self.assertIn("investigating", status_changes[0]["content"])
        self.assertEqual(status_changes[0]["actor_email"], "dev@trazeiq.io")

    def test_patch_without_status_change_logs_nothing(self):
        self.add_event(message="boom")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]

        response = self.client.patch(
            f"/api/v1/incidents/{incident_id}/",
            {"status": "open"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        entries = self.client.get(
            f"/api/v1/incidents/{incident_id}/timeline/"
        ).data["data"]["entries"]
        self.assertTrue(all(e["kind"] == "event" for e in entries))

    def test_resolve_appends_status_change(self):
        self.add_event(message="boom")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]

        response = self.client.post(f"/api/v1/incidents/{incident_id}/resolve/")
        self.assertEqual(response.status_code, 200)

        entries = self.client.get(
            f"/api/v1/incidents/{incident_id}/timeline/"
        ).data["data"]["entries"]
        status_changes = [e for e in entries if e["kind"] == "status_change"]
        self.assertEqual(len(status_changes), 1)
        self.assertEqual(status_changes[0]["actor_email"], "dev@trazeiq.io")

    def test_repeated_resolve_logs_single_entry(self):
        self.add_event(message="boom")
        incident_id = self.list_incidents().data["data"]["incidents"][0]["id"]

        self.client.post(f"/api/v1/incidents/{incident_id}/resolve/")
        self.client.post(f"/api/v1/incidents/{incident_id}/resolve/")

        entries = self.client.get(
            f"/api/v1/incidents/{incident_id}/timeline/"
        ).data["data"]["entries"]
        status_changes = [e for e in entries if e["kind"] == "status_change"]
        self.assertEqual(len(status_changes), 1)
