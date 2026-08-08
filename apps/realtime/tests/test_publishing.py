"""Phase 3A: publishing hooks.

DoD verification:
- triggering a new incident publishes ``incident.created`` on the project's
  private channel with the serialized incident,
- repeat occurrences publish ``incident.updated``,
- ``ai_analysis.ready`` fires when the analysis task completes successfully,
- resolving an incident publishes ``incident.resolved``,
- publishing is best-effort: unconfigured Pusher never breaks ingestion.
"""

from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.events.services import ingest_event
from apps.incidents.models import Incident
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.projects.models import Project

from apps.ai.models import AIAnalysis

PASSWORD = "Password123!"


class PusherPublishingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com", password=PASSWORD, email_verified=True
        )
        self.org = Organization.objects.create(name="Acme Inc", owner=self.user)
        Membership.objects.create(
            user=self.user, organization=self.org, role=MembershipRole.OWNER
        )
        self.project = Project.objects.create(organization=self.org, name="API")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("apps.realtime.pusher.publish", return_value=True)
    def test_new_incident_publishes_created_with_payload(self, publish):
        event = ingest_event(
            self.project, message="DatabaseError: connection refused", level="fatal"
        )
        incident = Incident.objects.get(error_group__events=event)
        publish.assert_called_once()
        channel, event_name, payload = publish.call_args.args
        self.assertEqual(channel, f"private-project-{self.project.id}")
        self.assertEqual(event_name, "incident.created")
        self.assertEqual(payload["incident"]["id"], str(incident.id))
        self.assertEqual(payload["incident"]["status"], "open")
        self.assertEqual(payload["incident"]["severity"], "critical")
        self.assertEqual(payload["incident"]["latest_event"]["id"], str(event.id))

    @patch("apps.realtime.pusher.publish", return_value=True)
    def test_repeat_occurrence_publishes_updated(self, publish):
        ingest_event(self.project, message="KeyError: 'user_id'")
        ingest_event(self.project, message="KeyError: 'user_id'")
        self.assertEqual(publish.call_count, 2)
        channel, event_name, _ = publish.call_args.args
        self.assertEqual(channel, f"private-project-{self.project.id}")
        self.assertEqual(event_name, "incident.updated")

    def test_unconfigured_pusher_never_fails_ingestion(self):
        with patch("apps.realtime.pusher.get_pusher") as get_pusher:
            client = Mock()
            client.trigger.side_effect = Exception("unreachable pusher")
            get_pusher.return_value = client
            event = ingest_event(self.project, message="Should not raise")
        self.assertIsNotNone(event.id)

    def test_analysis_ready_fires_after_successful_run(self):
        event = ingest_event(self.project, message="Slow query detected")
        incident = Incident.objects.get(error_group__events=event)
        analysis = AIAnalysis.objects.create(
            incident=incident, status=AIAnalysis.Status.READY, confidence="high",
            root_cause="Index missing", suggested_fix="Add an index",
            model_used="openai/gpt-oss-20b:free",
        )
        with override_settings(OPENROUTER_API_KEY="test-key"):
            with patch("apps.ai.tasks.run_analysis", return_value=analysis):
                with patch("apps.realtime.pusher.publish", return_value=True) as publish:
                    from apps.ai.tasks import analyze_incident
                    analyze_incident(str(incident.id))
        publish.assert_called_once()
        channel, event_name, payload = publish.call_args.args
        self.assertEqual(channel, f"private-project-{self.project.id}")
        self.assertEqual(event_name, "ai_analysis.ready")
        self.assertEqual(payload["analysis"]["id"], str(analysis.id))
        self.assertEqual(payload["incident"]["id"], str(incident.id))

    @patch("apps.realtime.pusher.publish", return_value=True)
    def test_resolve_publishes_resolved_and_flips_status(self, publish):
        event = ingest_event(self.project, message="OOM killed")
        incident = Incident.objects.get(error_group__events=event)
        publish.reset_mock()

        response = self.client.post(f"/api/v1/incidents/{incident.id}/resolve/")
        self.assertEqual(response.status_code, 200)
        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.RESOLVED)
        self.assertIsNotNone(incident.resolved_at)
        self.assertEqual(
            incident.timeline_entries.filter(kind="status_change").count(), 1
        )
        publish.assert_called_once()
        channel, event_name, payload = publish.call_args.args
        self.assertEqual(channel, f"private-project-{self.project.id}")
        self.assertEqual(event_name, "incident.resolved")
        self.assertEqual(payload["incident"]["status"], "resolved")

    @patch("apps.realtime.pusher.publish", return_value=True)
    def test_resolve_is_idempotent(self, publish):
        event = ingest_event(self.project, message="OOM killed")
        incident = Incident.objects.get(error_group__events=event)
        publish.reset_mock()
        self.client.post(f"/api/v1/incidents/{incident.id}/resolve/")
        self.client.post(f"/api/v1/incidents/{incident.id}/resolve/")
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(
            incident.timeline_entries.filter(kind="status_change").count(), 1
        )

    def test_resolve_foreign_incident_is_404(self):
        other = User.objects.create_user(
            email="intruder@example.com", password=PASSWORD, email_verified=True
        )
        other_org = Organization.objects.create(name="Intruder", owner=other)
        Membership.objects.create(
            user=other, organization=other_org, role=MembershipRole.OWNER
        )
        other_project = Project.objects.create(organization=other_org, name="Theirs")
        event = ingest_event(other_project, message="Theirs broke")
        incident = Incident.objects.get(error_group__events=event)
        response = self.client.post(f"/api/v1/incidents/{incident.id}/resolve/")
        self.assertEqual(response.status_code, 404)
        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.OPEN)
