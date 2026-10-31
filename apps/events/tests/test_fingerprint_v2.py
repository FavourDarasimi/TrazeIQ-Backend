import uuid
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.auditlog.models import AuditAction, AuditLog
from apps.events.models import ErrorGroup, Event
from apps.events.services import ingest_event, regroup_project_error_groups
from apps.events.utils import (
    fingerprint,
    is_in_app_frame,
    normalize_chunk_filename,
    parse_stacktrace_frames,
)
from apps.incidents.models import Incident
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.projects.models import Project

PASSWORD = "fdsK9Qop21z!"


def register_and_login(client: APIClient, email: str) -> None:
    client.post(
        "/api/v1/auth/register/request-otp/", {"email": email}, format="json"
    )
    verified = client.post(
        "/api/v1/auth/register/verify-otp/",
        {"email": email, "otp": "000000"},
        format="json",
    )
    client.post(
        "/api/v1/auth/register/complete/",
        {
            "registration_token": verified.data["data"]["registration_token"],
            "username": email.split("@")[0],
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        format="json",
    )


class InAppDetectionAndChunkNormalizationTests(TestCase):
    def test_chunk_hash_normalization(self):
        self.assertEqual(normalize_chunk_filename("main-4f81b.js"), "main.js")
        self.assertEqual(
            normalize_chunk_filename("main-4f81b17a.js"), "main.js"
        )
        self.assertEqual(
            normalize_chunk_filename("/_next/static/chunks/framework-8b3a72.js"),
            "/_next/static/chunks/framework.js",
        )
        self.assertEqual(
            normalize_chunk_filename("834-a1b2c3d4e5f6.js"), "834.js"
        )
        self.assertEqual(
            normalize_chunk_filename("main-4f81b.js?v=42"), "main.js"
        )
        self.assertEqual(
            normalize_chunk_filename("/app/services/billing.py"),
            "/app/services/billing.py",
        )
        self.assertEqual(
            normalize_chunk_filename("webpack-internal:///./components/Button.tsx"),
            "./components/Button.tsx",
        )

    def test_in_app_heuristics_vendor(self):
        # node_modules
        self.assertFalse(
            is_in_app_frame("/app/node_modules/express/lib/router.js")
        )
        self.assertFalse(
            is_in_app_frame("node_modules/react-dom/cjs/react-dom.production.min.js")
        )

        # Python site-packages
        self.assertFalse(
            is_in_app_frame(
                "/app/.venv/lib/python3.11/site-packages/django/core/handlers/wsgi.py"
            )
        )
        self.assertFalse(
            is_in_app_frame("/usr/local/lib/python3.10/dist-packages/requests/api.py")
        )

        # Python stdlib
        self.assertFalse(
            is_in_app_frame("/usr/lib/python3.11/wsgiref/handlers.py")
        )
        self.assertFalse(is_in_app_frame("<frozen importlib._bootstrap>"))
        self.assertFalse(is_in_app_frame("<built-in method>"))

        # Node stdlib
        self.assertFalse(is_in_app_frame("node:internal/process/task_queues"))
        self.assertFalse(is_in_app_frame("node:net"))

        # Next.js chunks & internals
        self.assertFalse(
            is_in_app_frame("/app/.next/static/chunks/main-4f81b17a.js")
        )
        self.assertFalse(
            is_in_app_frame("/_next/static/chunks/framework-98a72c.js")
        )
        self.assertFalse(
            is_in_app_frame("node_modules/next/dist/server/render.js")
        )

    def test_in_app_heuristics_app_code(self):
        self.assertTrue(is_in_app_frame("/app/services/billing.py"))
        self.assertTrue(is_in_app_frame("src/components/Header.tsx"))
        self.assertTrue(
            is_in_app_frame("webpack-internal:///./components/Button.tsx")
        )
        self.assertTrue(is_in_app_frame("/home/deploy/repo/handlers/user.go"))


class FingerprintV2Tests(TestCase):
    def test_fingerprint_v1_backwards_compatible(self):
        msg = "ValueError: test"
        trace = "  File \"test.py\", line 10, in foo\n"
        fp_v1 = fingerprint(message=msg, stacktrace=trace, version=1)
        self.assertEqual(len(fp_v1), 64)
        # Calling version=1 repeatedly yields identical output
        self.assertEqual(
            fp_v1, fingerprint(message=msg, stacktrace=trace, version=1)
        )

    def test_v2_line_number_immunity(self):
        trace_line_84 = """Traceback (most recent call last):
  File "/app/services/billing.py", line 84, in charge_customer
    raise PaymentError("card declined")"""

        trace_line_95 = """Traceback (most recent call last):
  File "/app/services/billing.py", line 95, in charge_customer
    raise PaymentError("card declined")"""

        fp1 = fingerprint(
            message="PaymentError: card declined",
            stacktrace=trace_line_84,
            version=2,
        )
        fp2 = fingerprint(
            message="PaymentError: card declined",
            stacktrace=trace_line_95,
            version=2,
        )
        self.assertEqual(fp1, fp2)

    def test_v2_vendor_frame_immunity(self):
        trace_shallow = """Traceback (most recent call last):
  File "/usr/lib/python3.11/wsgiref/handlers.py", line 137, in run
    self.result = application(self.environ, self.start_response)
  File "/app/.venv/lib/python3.11/site-packages/django/core/handlers/wsgi.py", line 133, in __call__
    response = self.get_response(request)
  File "/app/services/billing.py", line 84, in charge_customer
    raise PaymentError("card declined")"""

        trace_deep = """Traceback (most recent call last):
  File "/usr/lib/python3.11/wsgiref/handlers.py", line 140, in run
    self.result = application(self.environ, self.start_response)
  File "/app/.venv/lib/python3.11/site-packages/django/core/handlers/base.py", line 55, in wrapper
    return get_response(request)
  File "/app/.venv/lib/python3.11/site-packages/django/core/handlers/wsgi.py", line 138, in __call__
    response = self.get_response(request)
  File "/app/services/billing.py", line 84, in charge_customer
    raise PaymentError("card declined")"""

        fp_shallow = fingerprint(
            message="PaymentError: card declined",
            stacktrace=trace_shallow,
            version=2,
        )
        fp_deep = fingerprint(
            message="PaymentError: card declined",
            stacktrace=trace_deep,
            version=2,
        )
        self.assertEqual(fp_shallow, fp_deep)

    def test_v2_nextjs_chunk_and_middleware_immunity(self):
        js_trace_1 = """Error: card declined
    at chargeCustomer (/app/services/billing.js:84:12)
    at dispatch (/app/node_modules/express/lib/router.js:45:3)
    at main (/app/.next/static/chunks/main-4f81b17a.js:10:20)"""

        js_trace_2 = """Error: card declined
    at chargeCustomer (/app/services/billing.js:102:4)
    at wrapper (/app/node_modules/express/lib/wrapper.js:12:1)
    at dispatch (/app/node_modules/express/lib/router.js:50:3)
    at main (/app/.next/static/chunks/main-99a8b17c.js:14:22)"""

        fp1 = fingerprint(
            message="Error: card declined", stacktrace=js_trace_1, version=2
        )
        fp2 = fingerprint(
            message="Error: card declined", stacktrace=js_trace_2, version=2
        )
        self.assertEqual(fp1, fp2)

    def test_v2_zero_in_app_frames_fallback(self):
        vendor_trace_1 = """Error: connect ECONNREFUSED
    at TCPConnectWrap.afterConnect (node:net:1494:16)
    at TCPConnectWrap.callbackTrampoline (node:internal/async_hooks:130:17)"""

        vendor_trace_2 = """Error: connect ECONNREFUSED
    at TCPConnectWrap.afterConnect (node:net:1520:16)
    at TCPConnectWrap.callbackTrampoline (node:internal/async_hooks:135:17)"""

        fp1 = fingerprint(
            message="Error: connect ECONNREFUSED",
            stacktrace=vendor_trace_1,
            version=2,
        )
        fp2 = fingerprint(
            message="Error: connect ECONNREFUSED",
            stacktrace=vendor_trace_2,
            version=2,
        )
        self.assertEqual(fp1, fp2)

    def test_v2_structured_frames_support(self):
        frames = [
            {
                "filename": "components/Button.tsx",
                "function": "handleClick",
                "lineno": 45,
                "in_app": True,
            },
            {
                "filename": "node_modules/react/index.js",
                "function": "dispatch",
                "lineno": 120,
                "in_app": False,
            },
        ]
        fp = fingerprint(
            message="TypeError: cannot read property",
            frames=frames,
            version=2,
        )
        self.assertEqual(len(fp), 64)


class IngestionAndRegroupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        email = f"owner_{uuid.uuid4().hex[:8]}@example.com"
        register_and_login(self.client, email)
        res = self.client.post(
            "/api/v1/organizations/", {"name": "Test Org"}, format="json"
        )
        self.org_id = res.data["data"]["organization"]["id"]
        res_proj = self.client.post(
            "/api/v1/projects/",
            {
                "organization": self.org_id,
                "name": "Backend App",
                "environment": "production",
            },
            format="json",
        )
        self.project_id = res_proj.data["data"]["project"]["id"]
        self.api_key = res_proj.data["data"]["api_key"]
        self.project = Project.objects.get(id=self.project_id)
        self.user = self.project.organization.owner

    def test_ingestion_sets_fingerprint_version_2(self):
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=self.api_key)
        resp = client.post(
            "/api/v1/events/",
            {
                "message": "PaymentError: card declined",
                "stacktrace": '  File "/app/services/billing.py", line 84, in charge_customer\n',
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        event_data = resp.data["data"]["event"]
        self.assertEqual(event_data["fingerprint_version"], 2)

        event = Event.objects.get(id=event_data["id"])
        self.assertEqual(event.fingerprint_version, 2)
        self.assertEqual(event.error_group.fingerprint_version, 2)

    def test_regroup_project_error_groups_service(self):
        # Simulate two events that were ingested under v1 algorithm and ended up in different groups
        # because of different line numbers or wrapper frames.
        trace1 = """Traceback (most recent call last):
  File "/usr/lib/python3.11/wsgiref/handlers.py", line 137, in run
  File "/app/services/billing.py", line 84, in charge_customer
    raise PaymentError("card declined")"""

        trace2 = """Traceback (most recent call last):
  File "/app/.venv/lib/python3.11/site-packages/django/core/handlers/base.py", line 55, in wrapper
  File "/app/services/billing.py", line 95, in charge_customer
    raise PaymentError("card declined")"""

        # Ingest both using v1 fingerprints to simulate legacy split groups
        fp_v1_1 = fingerprint(
            message="PaymentError: card declined", stacktrace=trace1, version=1
        )
        fp_v1_2 = fingerprint(
            message="PaymentError: card declined", stacktrace=trace2, version=1
        )
        self.assertNotEqual(fp_v1_1, fp_v1_2)

        now = self.project.created_at
        g1 = ErrorGroup.objects.create(
            project=self.project,
            fingerprint=fp_v1_1,
            fingerprint_version=1,
            title="PaymentError: card declined",
            count=1,
            first_seen=now,
            last_seen=now,
        )
        g2 = ErrorGroup.objects.create(
            project=self.project,
            fingerprint=fp_v1_2,
            fingerprint_version=1,
            title="PaymentError: card declined",
            count=1,
            first_seen=now,
            last_seen=now,
        )

        e1 = Event.objects.create(
            project=self.project,
            error_group=g1,
            message="PaymentError: card declined",
            stacktrace=trace1,
            fingerprint=fp_v1_1,
            fingerprint_version=1,
        )
        e2 = Event.objects.create(
            project=self.project,
            error_group=g2,
            message="PaymentError: card declined",
            stacktrace=trace2,
            fingerprint=fp_v1_2,
            fingerprint_version=1,
        )

        inc1 = Incident.objects.create(
            project=self.project,
            error_group=g1,
            status=Incident.Status.OPEN,
        )
        inc2 = Incident.objects.create(
            project=self.project,
            error_group=g2,
            status=Incident.Status.OPEN,
        )

        self.assertEqual(
            ErrorGroup.objects.filter(project=self.project).count(), 2
        )

        # Dry run regroup
        dry_res = regroup_project_error_groups(
            self.project, actor=self.user, dry_run=True
        )
        self.assertTrue(dry_res["dry_run"])
        self.assertEqual(dry_res["groups_before"], 2)
        self.assertEqual(dry_res["groups_after"], 1)
        self.assertEqual(dry_res["groups_consolidated"], 1)
        # Groups unchanged after dry run
        self.assertEqual(
            ErrorGroup.objects.filter(project=self.project).count(), 2
        )

        # Actual regroup
        res = regroup_project_error_groups(
            self.project, actor=self.user, dry_run=False
        )
        self.assertFalse(res["dry_run"])
        self.assertEqual(res["groups_before"], 2)
        self.assertEqual(res["groups_after"], 1)
        self.assertEqual(res["groups_consolidated"], 1)

        # Now only 1 group exists
        self.assertEqual(
            ErrorGroup.objects.filter(project=self.project).count(), 1
        )
        canonical_group = ErrorGroup.objects.get(project=self.project)
        self.assertEqual(canonical_group.fingerprint_version, 2)
        self.assertEqual(canonical_group.count, 2)

        # Both events reassigned
        e1.refresh_from_db()
        e2.refresh_from_db()
        self.assertEqual(e1.error_group, canonical_group)
        self.assertEqual(e2.error_group, canonical_group)
        self.assertEqual(e1.fingerprint_version, 2)
        self.assertEqual(e2.fingerprint_version, 2)

        # Incidents: exactly 1 OPEN incident remains for canonical_group
        open_incidents = Incident.objects.filter(
            error_group=canonical_group, status=Incident.Status.OPEN
        )
        self.assertEqual(open_incidents.count(), 1)
        resolved_incidents = Incident.objects.filter(
            error_group=canonical_group, status=Incident.Status.RESOLVED
        )
        self.assertEqual(resolved_incidents.count(), 1)

        # Audit log was created
        log = AuditLog.objects.filter(
            organization=self.project.organization,
            action=AuditAction.GROUPS_REGROUPED,
        ).first()
        self.assertIsNotNone(log)

    def test_regroup_endpoint(self):
        resp = self.client.post(
            f"/api/v1/projects/{self.project_id}/regroup/",
            {"dry_run": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("groups_consolidated", resp.data["data"])
        self.assertIn("events_reprocessed", resp.data["data"])

    def test_regroup_errors_management_command(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command(
            "regroup_errors",
            f"--project={self.project_id}",
            "--dry-run",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("Starting regroup", output)
        self.assertIn("Backend App", output)
        self.assertIn("Done. Reprocessed", output)
