"""Phase 4A RBAC: role enforcement across the Phase 1–3 endpoints.

DoD: viewer is read-only (403 on any mutation, 200 on GETs); developer can
manage incidents (PATCH/resolve) but is denied org/project management
(rotate-key, invite, project writes); all enforced at the permission-class
level.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.incidents.models import TimelineEntry
from apps.organizations.models import Membership, MembershipRole
from apps.projects.models import Project

from apps.events.tests.test_events import create_org, create_project, register_and_login

User = get_user_model()


class RbacSetupMixin(TestCase):
    """An org with an incident, plus viewer/developer/admin members."""

    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")
        self.project = create_project(self.owner, org=self.org_id)
        self.project_id = self.project["id"]

        self.owner.post(
            "/api/v1/events/",
            {"message": "KeyError: boom", "level": "error"},
            format="json",
            headers={"X-API-Key": self.project["api_key"]},
        )
        self.incident_id = self.owner.get("/api/v1/incidents/").data["data"][
            "incidents"
        ][0]["id"]

        self.roles = {}
        for role in ("viewer", "developer", "admin"):
            client = APIClient()
            register_and_login(client, f"{role}@trazeiq.io")
            Membership.objects.create(
                user=User.objects.get(email=f"{role}@trazeiq.io"),
                organization_id=self.org_id,
                role=role,
            )
            self.roles[role] = client

    def assert_denied(self, response, code="PERMISSION_DENIED"):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], code)


class ViewerReadOnlyTests(RbacSetupMixin):
    def test_viewer_can_read_incidents(self):
        response = self.roles["viewer"].get("/api/v1/incidents/")
        self.assertEqual(response.status_code, 200)
        ids = [i["id"] for i in response.data["data"]["incidents"]]
        self.assertIn(self.incident_id, ids)

        detail = self.roles["viewer"].get(f"/api/v1/incidents/{self.incident_id}/")
        self.assertEqual(detail.status_code, 200)

        timeline = self.roles["viewer"].get(
            f"/api/v1/incidents/{self.incident_id}/timeline/"
        )
        self.assertEqual(timeline.status_code, 200)

        analysis = self.roles["viewer"].get(
            f"/api/v1/incidents/{self.incident_id}/analysis/"
        )
        self.assertEqual(analysis.status_code, 404)  # no analysis yet, but no 403

    def test_viewer_cannot_patch_incident(self):
        response = self.roles["viewer"].patch(
            f"/api/v1/incidents/{self.incident_id}/",
            {"status": "investigating"},
            format="json",
        )
        self.assert_denied(response)

    def test_viewer_cannot_resolve_incident(self):
        response = self.roles["viewer"].post(
            f"/api/v1/incidents/{self.incident_id}/resolve/", format="json"
        )
        self.assert_denied(response)

    def test_viewer_cannot_trigger_analysis(self):
        response = self.roles["viewer"].post(
            f"/api/v1/incidents/{self.incident_id}/analyze/", format="json"
        )
        self.assert_denied(response)


class DeveloperIncidentAccessTests(RbacSetupMixin):
    def test_developer_can_update_status(self):
        response = self.roles["developer"].patch(
            f"/api/v1/incidents/{self.incident_id}/",
            {"status": "investigating"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        incident = response.data["data"]["incident"]
        self.assertEqual(incident["status"], "investigating")
        self.assertEqual(incident["severity"], "high")  # untouched

        # The status change is recorded on the timeline.
        timeline = self.roles["developer"].get(
            f"/api/v1/incidents/{self.incident_id}/timeline/"
        )
        self.assertEqual(timeline.status_code, 200)
        self.assertTrue(
            TimelineEntry.objects.filter(
                incident_id=self.incident_id,
                kind=TimelineEntry.Kind.STATUS_CHANGE,
                actor__email="developer@trazeiq.io",
            ).exists()
        )

    def test_developer_can_assign_to_member(self):
        assignee = User.objects.get(email="viewer@trazeiq.io")
        response = self.roles["developer"].patch(
            f"/api/v1/incidents/{self.incident_id}/",
            {"assigned_to": str(assignee.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        incident = response.data["data"]["incident"]
        self.assertEqual(incident["assigned_to"], str(assignee.id))
        self.assertEqual(incident["assigned_to_email"], "viewer@trazeiq.io")

    def test_developer_cannot_assign_to_outsider(self):
        outsider = APIClient()
        register_and_login(outsider, "outsider@trazeiq.io")
        outsider_user = User.objects.get(email="outsider@trazeiq.io")

        response = self.roles["developer"].patch(
            f"/api/v1/incidents/{self.incident_id}/",
            {"assigned_to": str(outsider_user.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")

    def test_developer_can_resolve(self):
        response = self.roles["developer"].post(
            f"/api/v1/incidents/{self.incident_id}/resolve/", format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["data"]["incident"]["status"], "resolved"
        )


class ProjectManagementRbacTests(RbacSetupMixin):
    def test_developer_cannot_rotate_api_key(self):
        response = self.roles["developer"].post(
            f"/api/v1/projects/{self.project_id}/rotate-key/", format="json"
        )
        self.assert_denied(response)

    def test_viewer_cannot_rotate_api_key(self):
        response = self.roles["viewer"].post(
            f"/api/v1/projects/{self.project_id}/rotate-key/", format="json"
        )
        self.assert_denied(response)

    def test_developer_cannot_update_or_delete_project(self):
        patch = self.roles["developer"].patch(
            f"/api/v1/projects/{self.project_id}/",
            {"name": "Hacked"},
            format="json",
        )
        self.assert_denied(patch)

        delete = self.roles["developer"].delete(f"/api/v1/projects/{self.project_id}/")
        self.assert_denied(delete)

    def test_developer_cannot_create_project(self):
        response = self.roles["developer"].post(
            "/api/v1/projects/",
            {"name": "Sneaky", "organization": self.org_id},
            format="json",
        )
        self.assert_denied(response)

    def test_developer_cannot_invite(self):
        response = self.roles["developer"].post(
            f"/api/v1/organizations/{self.org_id}/invite/",
            {"email": "nobody@trazeiq.io"},
            format="json",
        )
        self.assert_denied(response)


class AdminAndOwnerTests(RbacSetupMixin):
    def test_admin_can_rotate_api_key_and_old_key_dies(self):
        response = self.roles["admin"].post(
            f"/api/v1/projects/{self.project_id}/rotate-key/", format="json"
        )
        self.assertEqual(response.status_code, 200)
        new_key = response.data["data"]["api_key"]
        self.assertNotEqual(new_key, self.project["api_key"])
        self.assertEqual(Project.objects.get(id=self.project_id).api_key_prefix, new_key[:8])

        # The old key stops authenticating immediately.
        stale = APIClient().post(
            "/api/v1/events/",
            {"message": "stale key"},
            format="json",
            headers={"X-API-Key": self.project["api_key"]},
        )
        self.assertEqual(stale.status_code, 401)

        # The new key works.
        fresh = APIClient().post(
            "/api/v1/events/",
            {"message": "fresh key"},
            format="json",
            headers={"X-API-Key": new_key},
        )
        self.assertEqual(fresh.status_code, 201)

    def test_admin_can_invite(self):
        response = self.roles["admin"].post(
            f"/api/v1/organizations/{self.org_id}/invite/",
            {"email": "someone@trazeiq.io", "role": "developer"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_owner_can_resolve_and_update(self):
        patch = self.owner.patch(
            f"/api/v1/incidents/{self.incident_id}/",
            {"severity": "critical"},
            format="json",
        )
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(patch.data["data"]["incident"]["severity"], "critical")


class RbacTenantSemanticsTests(RbacSetupMixin):
    def test_foreign_org_rotate_key_is_404_not_403(self):
        """Not a member → the view 404s; the existence of the project is not
        leaked via a 403 (same contract as every tenant getter)."""
        outsider = APIClient()
        register_and_login(outsider, "outsider@trazeiq.io")
        response = outsider.post(
            f"/api/v1/projects/{self.project_id}/rotate-key/", format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_foreign_org_invite_is_404(self):
        outsider = APIClient()
        register_and_login(outsider, "outsider@trazeiq.io")
        response = outsider.post(
            f"/api/v1/organizations/{self.org_id}/invite/",
            {"email": "nobody@trazeiq.io"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_foreign_incident_patch_is_404(self):
        outsider = APIClient()
        register_and_login(outsider, "outsider@trazeiq.io")
        response = outsider.patch(
            f"/api/v1/incidents/{self.incident_id}/",
            {"status": "resolved"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
