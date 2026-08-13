"""Phase 5B: AuditLog model, the audit-log endpoint, and audit entries.

DoD verification:
- GET /api/v1/audit-logs/ returns 403 for a developer and 200 for an owner/admin,
- rotating a project API key writes a key_rotated entry,
- removing a member writes a member_removed entry,
- resolving an incident writes an incident_resolved entry,
- an owner only sees logs for orgs they administer.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.auditlog.models import AuditAction, AuditLog
from apps.events.services import ingest_event
from apps.incidents.models import Incident
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.projects.services import create_project

PASSWORD = "Password123!"


def make_user(email, org=None, role=None):
    user = User.objects.create_user(
        email=email, password=PASSWORD, email_verified=True
    )
    if org is not None and role is not None:
        Membership.objects.create(user=user, organization=org, role=role)
    return user


class AuditLogEndpointTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.org = Organization.objects.create(name="Acme", owner=self.owner)
        Membership.objects.create(
            user=self.owner, organization=self.org, role=MembershipRole.OWNER
        )
        self.dev = make_user("dev@example.com", org=self.org, role=MembershipRole.DEVELOPER)
        self.project, _ = create_project(
            organization=self.org, name="API", environment="production"
        )
        self.client = APIClient()

    def test_developer_gets_403(self):
        self.client.force_authenticate(user=self.dev)
        res = self.client.get("/api/v1/audit-logs/")
        self.assertEqual(res.status_code, 403)

    def test_owner_gets_200(self):
        self.client.force_authenticate(user=self.owner)
        res = self.client.get("/api/v1/audit-logs/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("audit_logs", res.data["data"])

    def test_rotate_key_writes_audit_log(self):
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            f"/api/v1/projects/{self.project.id}/rotate-key/"
        )
        self.assertEqual(res.status_code, 200)

        log = AuditLog.objects.get()
        self.assertEqual(log.action, AuditAction.KEY_ROTATED)
        self.assertEqual(log.actor, self.owner)
        self.assertEqual(log.organization, self.org)
        self.assertIn("API", log.target)

    def test_remove_member_writes_audit_log(self):
        victim = make_user(
            "victim@example.com", org=self.org, role=MembershipRole.DEVELOPER
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.delete(
            f"/api/v1/organizations/{self.org.id}/members/{victim.id}/"
        )
        self.assertEqual(res.status_code, 200)

        log = AuditLog.objects.get()
        self.assertEqual(log.action, AuditAction.MEMBER_REMOVED)
        self.assertEqual(log.actor, self.owner)
        self.assertIn("victim@example.com", log.target)

    def test_resolve_incident_writes_audit_log(self):
        event = ingest_event(self.project, message="Boom", level="error")
        incident = Incident.objects.get(error_group=event.error_group)

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(f"/api/v1/incidents/{incident.id}/resolve/")
        self.assertEqual(res.status_code, 200)

        log = AuditLog.objects.get()
        self.assertEqual(log.action, AuditAction.INCIDENT_RESOLVED)
        self.assertEqual(log.actor, self.owner)
        self.assertIn(incident.error_group.title, log.target)

    def test_owner_only_sees_admin_org_logs(self):
        # A second org where the owner is only a developer (not admin).
        other_org = Organization.objects.create(name="Other", owner=self.dev)
        Membership.objects.create(
            user=self.owner, organization=other_org, role=MembershipRole.DEVELOPER
        )
        victim = make_user(
            "v2@example.com", org=other_org, role=MembershipRole.DEVELOPER
        )
        self.client.force_authenticate(user=self.dev)
        self.client.delete(
            f"/api/v1/organizations/{other_org.id}/members/{victim.id}/"
        )

        self.client.force_authenticate(user=self.owner)
        res = self.client.get("/api/v1/audit-logs/")
        self.assertEqual(res.status_code, 200)
        actions = [entry["action"] for entry in res.data["data"]["audit_logs"]]
        self.assertNotIn(AuditAction.MEMBER_REMOVED, actions)
