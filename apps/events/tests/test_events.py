"""Phase 1D: event ingestion — API-key auth, redaction, dedup, tenant scoping."""

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from ..models import ErrorGroup, Event
from ..utils import fingerprint, redact_secrets
from apps.incidents.models import Incident

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
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        format="json",
    )


def create_org(client: APIClient, name: str) -> str:
    response = client.post(
        "/api/v1/organizations/", {"name": name}, format="json"
    )
    return response.data["data"]["organization"]["id"]


def create_project(client: APIClient, *, name="Web", org=None) -> dict:
    org = org or create_org(client, "Acme")
    response = client.post(
        "/api/v1/projects/", {"name": name, "organization": org}, format="json"
    )
    return {
        "id": response.data["data"]["project"]["id"],
        "api_key": response.data["data"]["api_key"],
    }


class IngestSetupMixin:
    def setUp(self):
        cache.clear()  # reset the per-email signup cap between test cases
        self.client = APIClient()
        register_and_login(self.client, "dev@trazeiq.io")
        self.project = create_project(self.client)
        self.auth_headers = {"X-API-Key": self.project["api_key"]}

    def ingest(self, payload=None, headers=None):
        effective_headers = self.auth_headers if headers is None else headers
        return self.client.post(
            "/api/v1/events/",
            payload
            or {"message": "ValueError: boom", "stacktrace": "Traceback..."},
            format="json",
            headers=effective_headers,
        )


class IngestAuthTests(IngestSetupMixin, TestCase):
    def test_requires_api_key(self):
        # Fresh client — no session cookies, like the real integration.
        anonymous = APIClient()
        response = anonymous.post(
            "/api/v1/events/", {"message": "boom"}, format="json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "NOT_AUTHENTICATED")

    def test_rejects_unknown_api_key(self):
        anonymous = APIClient()
        response = anonymous.post(
            "/api/v1/events/",
            {"message": "boom"},
            format="json",
            headers={"X-API-Key": "deadbeef" * 8},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "NOT_AUTHENTICATED")

    def test_accepts_valid_api_key(self):
        response = self.ingest()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["success"], True)
        self.assertIn("event", response.data["data"])

    def test_does_not_accept_a_user_session(self):
        # A logged-in browser session must not authenticate ingestion.
        unauthenticated = self.client.post(
            "/api/v1/events/", {"message": "x"}, format="json"
        )
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(
            unauthenticated.data["error"]["code"], "NOT_AUTHENTICATED"
        )


class IngestPayloadTests(IngestSetupMixin, TestCase):
    def test_validates_required_message(self):
        response = self.ingest(payload={"level": "error"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")

    def test_rejects_unknown_level(self):
        response = self.ingest(payload={"message": "x", "level": "oops"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")

    def test_defaults_environment_from_project(self):
        response = self.ingest()
        event = Event.objects.get()
        self.assertEqual(event.environment, "production")
        self.assertEqual(response.data["data"]["event"]["environment"], "production")

    def test_defaults_level_to_error(self):
        self.ingest()
        event = Event.objects.get()
        self.assertEqual(event.level, Event.Level.ERROR)


class IngestRedactionTests(IngestSetupMixin, TestCase):
    """Agent.md rule 5: raw error content is untrusted; secrets never stored."""

    def test_stores_redacted_message(self):
        self.ingest(
            payload={
                "message": "Connection failed SECRET_KEY=supersecret123",
                "stacktrace": (
                    "raise ValueError('x')\n"
                    "  File /app/main.py:42 in run_worker\n"
                    "  token=abc123"
                ),
            }
        )
        event = Event.objects.get()
        self.assertNotIn("supersecret123", event.message)
        self.assertNotIn("abc123", event.stacktrace)
        self.assertIn("SECRET_KEY=[REDACTED]", event.message)
        self.assertIn("token=[REDACTED]", event.stacktrace)

    def test_bearer_and_jwt_are_redacted(self):
        self.ingest(
            payload={
                "message": "401 from api.Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
            }
        )
        event = Event.objects.get()
        self.assertNotIn("eyJhbGci", event.message)
        self.assertNotIn("Bearer ", event.message.replace("Bearer [REDACTED]", ""))

    def test_redact_secrets_utility(self):
        self.assertEqual(
            redact_secrets("secret_key=s3cret password='hunter2'"),
            "SECRET_KEY=[REDACTED] password='[REDACTED]'",
        )
        self.assertEqual(
            redact_secrets("DATABASE_URL=postgres://u:p@h/db"),
            "DATABASE_URL=[REDACTED]",
        )
        self.assertEqual(redact_secrets("plain text"), "plain text")


class IngestDedupTests(IngestSetupMixin, TestCase):
    """Agent.md rule 3: repeats collapse into one ErrorGroup, count bumps."""

    @override_settings(EVENT_THROTTLE_KEY="1000/min")
    def test_identical_events_dedup_into_one_group(self):
        for _ in range(50):
            self.ingest()
        self.assertEqual(ErrorGroup.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 50)
        group = ErrorGroup.objects.get()
        self.assertEqual(group.count, 50)

    def test_different_errors_get_different_groups(self):
        self.ingest(payload={"message": "ValueError: A"})
        self.ingest(payload={"message": "ValueError: B"})
        self.assertEqual(ErrorGroup.objects.count(), 2)

    def test_line_numbers_do_not_split_groups(self):
        self.ingest(payload={"message": "TypeError: t", "stacktrace": "f.py:10"})
        self.ingest(payload={"message": "TypeError: t", "stacktrace": "f.py:777"})
        self.assertEqual(ErrorGroup.objects.count(), 1)
        self.assertEqual(ErrorGroup.objects.get().count, 2)

    def test_first_ingest_reuses_open_incident(self):
        self.ingest()
        self.ingest()
        incident = Incident.objects.get()
        self.assertEqual(
            incident.status, Incident.Status.OPEN
        )
        self.assertEqual(incident.error_group.count, 2)


class Ingest413Tests(IngestSetupMixin, TestCase):
    def test_payload_over_cap_rejected(self):
        response = self.client.post(
            "/api/v1/events/",
            {"message": "x" * (100_000 + 1)},
            format="json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(ErrorGroup.objects.count(), 0)

    @override_settings(EVENT_MAX_PAYLOAD_BYTES=1024)
    def test_payload_at_cap_is_accepted(self):
        # Exactly at the cap (message + stacktrace) must succeed.
        response = self.client.post(
            "/api/v1/events/",
            {"message": "x" * 1024, "stacktrace": ""},
            format="json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Event.objects.count(), 1)

    @override_settings(EVENT_MAX_PAYLOAD_BYTES=1024)
    def test_payload_one_byte_over_cap_rejected(self):
        # One byte over the cap is rejected, with nothing persisted.
        response = self.client.post(
            "/api/v1/events/",
            {"message": "x" * 1025, "stacktrace": ""},
            format="json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.data["error"]["code"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(Event.objects.count(), 0)


class IngestThrottleTests(IngestSetupMixin, TestCase):
    """Phase 5A: Redis-backed per-key/per-IP caps with Retry-After."""

    @override_settings(EVENT_THROTTLE_KEY="100000/min")
    def test_per_key_limit_returns_429_with_retry_after(self):
        # The per-project cap (set low) is the one enforced on the key.
        from apps.projects.models import Project

        Project.objects.filter(id=self.project["id"]).update(events_per_minute=3)
        for _ in range(3):
            ok = self.ingest()
            self.assertEqual(ok.status_code, 201)
        blocked = self.ingest()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.data["error"]["code"], "TOO_MANY_REQUESTS")
        self.assertIn("Retry-After", blocked.headers)
        self.assertTrue(int(blocked.headers["Retry-After"]) > 0)
        self.assertLessEqual(Event.objects.count(), 3)

    @override_settings(EVENT_THROTTLE_KEY="100000/min")
    def test_per_project_limit_overrides_global(self):
        # Lower the project's own cap; it must win over the global setting.
        from apps.projects.models import Project

        project = Project.objects.get(id=self.project["id"])
        project.events_per_minute = 2
        project.save(update_fields=["events_per_minute"])

        for _ in range(2):
            ok = self.ingest()
            self.assertEqual(ok.status_code, 201)
        blocked = self.ingest()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.data["error"]["code"], "TOO_MANY_REQUESTS")

    @override_settings(EVENT_THROTTLE_KEY="100000/min", EVENT_THROTTLE_IP="2/min")
    def test_per_ip_limit_is_independent_of_key(self):
        # A second project under the same IP still hits the IP cap at 2.
        other = create_project(self.client, name="Other")
        other_headers = {"X-API-Key": other["api_key"]}

        first = self.ingest()
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            "/api/v1/events/", {"message": "boom"}, format="json", headers=other_headers
        )
        self.assertEqual(second.status_code, 201)
        # Third ingest from either key on this IP is blocked by the IP cap.
        third = self.ingest()
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.data["error"]["code"], "TOO_MANY_REQUESTS")


class EventTenantIsolationTests(TestCase):
    """Agent.md rule 2: events are invisible across organizations."""

    def setUp(self):
        cache.clear()
        self.alice = APIClient()
        register_and_login(self.alice, "alice@trazeiq.io")
        alice_project = create_project(self.alice)
        self.alice_event_id = self.alice.post(
            "/api/v1/events/",
            {"message": "alice boom"},
            format="json",
            headers={"X-API-Key": alice_project["api_key"]},
        ).data["data"]["event"]["id"]

        self.bob = APIClient()
        register_and_login(self.bob, "bob@example.io")

    def test_cannot_list_another_orgs_events(self):
        bob_list = self.bob.get("/api/v1/events/")
        self.assertEqual(bob_list.status_code, 200)
        ids = [e["id"] for e in bob_list.data["data"]["events"]]
        self.assertNotIn(self.alice_event_id, ids)

    def test_cross_org_detail_is_404(self):
        response = self.bob.get(
            f"/api/v1/events/{self.alice_event_id}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_still_see_own_event(self):
        response = self.alice.get(
            f"/api/v1/events/{self.alice_event_id}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["data"]["event"]["id"], self.alice_event_id
        )


class EventListFilterTests(IngestSetupMixin, TestCase):
    def _seed(self):
        self.ingest(payload={"message": "A", "level": "error", "environment": "prod"})
        self.ingest(payload={"message": "B", "level": "warning", "environment": "staging"})

    def test_filter_by_level(self):
        self._seed()
        response = self.client.get("/api/v1/events/?level=warning")
        self.assertEqual(response.status_code, 200)
        messages = {e["message"] for e in response.data["data"]["events"]}
        self.assertEqual(messages, {"B"})

    def test_invalid_date_is_400(self):
        response = self.client.get("/api/v1/events/?date=not-a-date")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "INVALID_DATE")


class FingerprintUtilsTests(TestCase):
    def test_fingerprint_is_deterministic_and_low_entropy(self):
        f1 = fingerprint(
            message="ValueError: bad", stacktrace="f.py:1\n0x7f\n"
        )
        f2 = fingerprint(
            message="ValueError: bad", stacktrace="f.py:9000\n0x9b\n"
        )
        self.assertEqual(f1, f2)

    def test_fingerprint_differs_across_errors(self):
        self.assertNotEqual(
            fingerprint(message="ValueError: bad"),
            fingerprint(message="TypeError: bad"),
        )
