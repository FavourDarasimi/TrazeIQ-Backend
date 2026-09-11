"""Platform admin endpoints (apps.platform).

Access control is the load-bearing behavior here, so it gets explicit
coverage on every endpoint:

- anonymous → 401
- authenticated non-staff → 403 (no existence leak either way)
- staff → 200 with the documented shape, platform-wide (cross-tenant)
- list payloads never leak secrets (no password hash, no api_key_hash)
- search + pagination behave
"""

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.auditlog.models import AuditAction, AuditLog
from apps.events.models import ErrorGroup, Event
from apps.incidents.models import Incident
from apps.organizations.models import Membership, MembershipRole, Organization

PASSWORD = "Password123!"

ENDPOINTS = [
    "/api/v1/admin/overview/",
    "/api/v1/admin/users/",
    "/api/v1/admin/organizations/",
    "/api/v1/admin/projects/",
    "/api/v1/admin/health/",
]


class PlatformAdminAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            email="staff@trazeiq.io",
            password=PASSWORD,
            email_verified=True,
            is_staff=True,
        )
        self.user = User.objects.create_user(
            email="dev@example.com",
            password=PASSWORD,
            email_verified=True,
        )

    def test_anonymous_gets_401(self):
        for url in ENDPOINTS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.data["error"]["code"], "NOT_AUTHENTICATED"
                )

    def test_non_staff_gets_403(self):
        self.client.force_authenticate(user=self.user)
        for url in ENDPOINTS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.data["error"]["code"], "PERMISSION_DENIED"
                )

    def test_staff_gets_200_everywhere(self):
        self.client.force_authenticate(user=self.staff)
        for url in ENDPOINTS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.data["success"])


class PlatformAdminDataTests(TestCase):
    def setUp(self):
        # The overview aggregate is TTL-cached in the process-global cache —
        # clear it so each test computes fresh (same reason a deploy or a
        # test run must never serve a previous test's numbers).
        cache.clear()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            email="staff@trazeiq.io",
            password=PASSWORD,
            email_verified=True,
            is_staff=True,
        )
        # Two tenants — the platform surface must see both (no tenant
        # scoping here, unlike every product endpoint).
        self.org_a = Organization.objects.create(
            name="Acme Inc", owner=self.staff
        )
        owner_b = User.objects.create_user(
            email="owner@rivals.io",
            password=PASSWORD,
            email_verified=True,
        )
        self.org_b = Organization.objects.create(
            name="Rivals Ltd", owner=owner_b
        )
        Membership.objects.create(
            user=self.staff,
            organization=self.org_a,
            role=MembershipRole.OWNER,
        )
        self.project = self.org_a.projects.create(
            name="payment-api",
            api_key_hash="digest",
            api_key_prefix="prefix12",
            environment="production",
        )
        now = timezone.now()
        group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp1",
            title="boom",
            count=1,
            first_seen=now,
            last_seen=now,
        )
        self.incident = Incident.objects.create(
            error_group=group,
            project=self.project,
            severity=Incident.Severity.CRITICAL,
            status=Incident.Status.OPEN,
        )
        Event.objects.create(
            project=self.project,
            error_group=group,
            message="boom",
            fingerprint="fp1",
        )
        AuditLog.objects.create(
            organization=self.org_a,
            actor=self.staff,
            action=AuditAction.KEY_ROTATED,
            target="payment-api",
        )
        self.client.force_authenticate(user=self.staff)

    def test_overview_shape_and_counts(self):
        data = self.client.get("/api/v1/admin/overview/").data["data"][
            "overview"
        ]
        self.assertEqual(data["users"], 2)
        self.assertEqual(data["organizations"], 2)
        self.assertEqual(data["projects"], 1)
        self.assertEqual(data["events_24h"], 1)
        self.assertEqual(data["open_incidents"], 1)
        self.assertEqual(data["open_incidents_by_severity"]["critical"], 1)
        self.assertEqual(len(data["top_projects"]), 1)
        self.assertEqual(data["top_projects"][0]["name"], "payment-api")
        self.assertEqual(data["top_projects"][0]["events_24h"], 1)
        self.assertEqual(len(data["recent_audit"]), 1)
        self.assertEqual(
            data["recent_audit"][0]["actor_email"], "staff@trazeiq.io"
        )

    def test_users_list_search_pagination_and_no_leak(self):
        response = self.client.get("/api/v1/admin/users/")
        users = response.data["data"]["users"]
        self.assertEqual(
            response.data["data"]["pagination"]["total"], 2
        )
        for row in users:
            self.assertNotIn("password", row)
            self.assertIn("is_staff", row)
            self.assertIn("org_count", row)

        searched = self.client.get(
            "/api/v1/admin/users/", {"search": "rivals"}
        ).data["data"]["users"]
        self.assertEqual(len(searched), 1)
        self.assertEqual(searched[0]["email"], "owner@rivals.io")

        paged = self.client.get(
            "/api/v1/admin/users/", {"page": 2, "page_size": 1}
        ).data["data"]
        self.assertEqual(len(paged["users"]), 1)
        self.assertTrue(paged["pagination"]["has_previous"])
        self.assertFalse(paged["pagination"]["has_next"])

    def test_organizations_list_sees_all_tenants(self):
        orgs = self.client.get("/api/v1/admin/organizations/").data["data"][
            "organizations"
        ]
        self.assertEqual(
            {row["name"] for row in orgs}, {"Acme Inc", "Rivals Ltd"}
        )
        acme = next(row for row in orgs if row["name"] == "Acme Inc")
        self.assertEqual(acme["owner_email"], "staff@trazeiq.io")
        self.assertEqual(acme["member_count"], 1)
        self.assertEqual(acme["project_count"], 1)

    def test_projects_list_exposes_prefix_not_hash(self):
        projects = self.client.get("/api/v1/admin/projects/").data["data"][
            "projects"
        ]
        self.assertEqual(len(projects), 1)
        row = projects[0]
        self.assertEqual(row["api_key_prefix"], "prefix12")
        self.assertNotIn("api_key_hash", row)
        self.assertEqual(row["events_24h"], 1)
        self.assertEqual(row["open_incidents"], 1)
        self.assertEqual(row["organization"], "Acme Inc")

        searched = self.client.get(
            "/api/v1/admin/projects/", {"search": "payment"}
        ).data["data"]["projects"]
        self.assertEqual(len(searched), 1)
        empty = self.client.get(
            "/api/v1/admin/projects/", {"search": "nope"}
        ).data["data"]["projects"]
        self.assertEqual(empty, [])

    def test_health_reports_status(self):
        data = self.client.get("/api/v1/admin/health/").data["data"]
        self.assertIn(data["status"], ("ok", "degraded"))
        self.assertIn("checks", data)
        self.assertIn("metrics", data)
