"""In-app notifications: inbox endpoints, unread counter, mark-read and
alert preferences, plus the delivery rules behind the hooks.

DoD: a new incident fan-out creates one inbox row per eligible org member;
assignment reaches the assignee; ``only_assigned_to_me`` silences everything
else; the actor never notifies themselves; mark-read (all or by ids) works;
preferences GET/PATCH round-trip; tenant isolation holds (a foreign user
sees none of it).
"""

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from uuid import uuid4

from apps.accounts.models import User
from apps.events.tests.test_events import create_project, register_and_login
from apps.incidents.models import Incident
from apps.organizations.models import Membership, MembershipRole


class NotificationSetupMixin:
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        register_and_login(self.client, "dev@trazeiq.io")
        self.owner = User.objects.get(email="dev@trazeiq.io")

        self.member_client = APIClient()
        register_and_login(self.member_client, "member@trazeiq.io")
        self.member = User.objects.get(email="member@trazeiq.io")

        self.project = create_project(self.client)
        self.org_id = Membership.objects.get(
            user=self.owner, role=MembershipRole.OWNER
        ).organization_id
        Membership.objects.create(
            user=self.member,
            organization_id=self.org_id,
            role=MembershipRole.DEVELOPER,
        )
        self.ingest_client = APIClient()

    def ingest(self, client=None, *, message="KeyError: boom"):
        client = client or self.ingest_client
        return client.post(
            "/api/v1/events/",
            {
                "message": message,
                "stacktrace": "Traceback (most recent call last):\n  raise KeyError('boom')",
            },
            headers={"X-API-Key": self.project["api_key"]},
            format="json",
        )

    def get_incident(self):
        return Incident.objects.get()

    def list_unread(self, client=None):
        client = client or self.client
        response = client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, 200)
        return response.data["data"]


class InboxTests(NotificationSetupMixin, TestCase):
    def test_new_incident_fans_out_to_every_member(self):
        self.ingest()
        for client in (self.client, self.member_client):
            data = self.list_unread(client)
            self.assertEqual(data["unread_count"], 1)
            notification = data["notifications"][0]
            self.assertEqual(notification["kind"], "incident_created")
            self.assertEqual(notification["is_read"], False)
            self.assertEqual(notification["title"], "New high incident")
            self.assertEqual(
                notification["incident"]["title"], "KeyError: boom"
            )

    def test_actor_never_notifies_self_on_comment(self):
        self.ingest()
        incident = self.get_incident()
        response = self.client.post(
            f"/api/v1/incidents/{incident.pk}/comments/",
            {"content": "Looking into this"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        owner_data = self.list_unread(self.client)
        member_data = self.list_unread(self.member_client)
        # Owner has created + commented-on-own? No — comments notify the
        # *other* member only; the owner keeps the one created notification.
        self.assertEqual(owner_data["unread_count"], 1)
        self.assertEqual(member_data["unread_count"], 2)
        kinds = {n["kind"] for n in member_data["notifications"]}
        self.assertEqual(
            kinds, {"incident_created", "incident_commented"}
        )

    def test_assignment_notifies_only_assignee(self):
        self.ingest()
        incident = self.get_incident()
        response = self.client.patch(
            f"/api/v1/incidents/{incident.pk}/",
            {"assigned_to": str(self.member.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        member_data = self.list_unread(self.member_client)
        self.assertEqual(member_data["unread_count"], 2)
        self.assertEqual(
            member_data["notifications"][0]["kind"], "incident_assigned"
        )
        self.assertEqual(
            member_data["notifications"][0]["title"], "Incident assigned to you"
        )

    def test_mark_read_all_and_by_ids(self):
        self.ingest()
        response = self.client.post(
            "/api/v1/notifications/read/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["marked"], 1)
        self.assertEqual(response.data["data"]["unread_count"], 0)

        # By ids, leaving the other unread.
        self.ingest(message="Second boom")
        self.ingest(message="Third boom")
        data = self.list_unread(self.client)
        ids = [n["id"] for n in data["notifications"]]
        response = self.client.post(
            "/api/v1/notifications/read/",
            {"ids": [ids[0]]},
            format="json",
        )
        self.assertEqual(response.data["data"]["marked"], 1)
        self.assertEqual(response.data["data"]["unread_count"], 1)

    def test_foreign_notifications_are_invisible(self):
        self.ingest()
        outsider = APIClient()
        register_and_login(outsider, "outsider@trazeiq.io")
        response = outsider.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["unread_count"], 0)
        self.assertEqual(response.data["data"]["notifications"], [])

    def test_unknown_read_targets_are_ignored(self):
        self.ingest()
        response = self.client.post(
            "/api/v1/notifications/read/",
            {"ids": [str(uuid4())]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["marked"], 0)


class PreferenceTests(NotificationSetupMixin, TestCase):
    def test_defaults_materialize_on_first_read(self):
        response = self.client.get("/api/v1/notifications/preferences/")
        self.assertEqual(response.status_code, 200)
        prefs = response.data["data"]["preferences"]
        self.assertEqual(prefs["only_assigned_to_me"], False)
        self.assertEqual(prefs["notify_on_new_incidents"], True)
        self.assertEqual(prefs["notify_on_status_changes"], True)
        self.assertEqual(prefs["notify_on_comments"], True)

    def test_patch_flips_single_knob(self):
        response = self.client.patch(
            "/api/v1/notifications/preferences/",
            {"only_assigned_to_me": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        prefs = response.data["data"]["preferences"]
        self.assertEqual(prefs["only_assigned_to_me"], True)
        self.assertEqual(prefs["notify_on_comments"], True)

    def test_assigned_only_silences_unassigned_activity(self):
        self.member_client.patch(
            "/api/v1/notifications/preferences/",
            {"only_assigned_to_me": True},
            format="json",
        )
        self.ingest()
        # New unassigned incident: member stays silent, owner gets it.
        self.assertEqual(self.list_unread(self.member_client)["unread_count"], 0)
        self.assertEqual(self.list_unread(self.client)["unread_count"], 1)

        # Assigning the incident then reaches only the assignee.
        incident = self.get_incident()
        response = self.client.patch(
            f"/api/v1/incidents/{incident.pk}/",
            {"assigned_to": str(self.member.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        member_data = self.list_unread(self.member_client)
        self.assertEqual(member_data["unread_count"], 1)
        self.assertEqual(
            member_data["notifications"][0]["kind"], "incident_assigned"
        )

    def test_comment_knob_off(self):
        self.member_client.patch(
            "/api/v1/notifications/preferences/",
            {"notify_on_comments": False},
            format="json",
        )
        self.ingest()
        incident = self.get_incident()
        self.client.post(
            f"/api/v1/incidents/{incident.pk}/comments/",
            {"content": "On it"},
            format="json",
        )
        self.assertEqual(self.list_unread(self.member_client)["unread_count"], 1)


class DeliveryRuleTests(NotificationSetupMixin, TestCase):
    def test_defaults_match_preference_defaults(self):
        # No preference row → same audience as the default row.
        self.ingest()
        data = self.list_unread(self.member_client)
        self.assertEqual(data["unread_count"], 1)

    def test_updated_notification_skips_actor(self):
        self.ingest()
        incident = self.get_incident()
        self.client.patch(
            f"/api/v1/incidents/{incident.pk}/",
            {"status": "investigating"},
            format="json",
        )
        owner_data = self.list_unread(self.client)
        member_data = self.list_unread(self.member_client)
        self.assertEqual(owner_data["unread_count"], 1)  # created only
        self.assertEqual(member_data["unread_count"], 2)  # created + updated
        self.assertEqual(
            member_data["notifications"][0]["kind"], "incident_updated"
        )
        self.assertIn("status", member_data["notifications"][0]["body"])

    def test_resolve_notifies_org_but_not_actor(self):
        self.ingest()
        incident = self.get_incident()
        response = self.client.post(
            f"/api/v1/incidents/{incident.pk}/resolve/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        owner_data = self.list_unread(self.client)
        member_data = self.list_unread(self.member_client)
        self.assertEqual(owner_data["unread_count"], 1)
        self.assertEqual(member_data["unread_count"], 2)
        self.assertEqual(
            member_data["notifications"][0]["kind"], "incident_resolved"
        )

    def test_repeat_event_does_not_duplicate_created_notification(self):
        self.ingest()
        self.ingest(message="KeyError: boom")
        self.assertEqual(
            self.list_unread(self.member_client)["unread_count"], 1
        )