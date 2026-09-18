"""Error-group listing + redaction/fingerprint regression tests.

Covers the review fixes: bare ``password=hunter2`` redaction (quotes
optional), ``line N`` fingerprint normalization, and the
``GET /api/v1/error-groups/`` endpoint (membership-scoped, JSON envelope).
"""

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.events.tests.test_events import create_project, register_and_login
from apps.events.utils import fingerprint, redact_secrets


class BarePasswordRedactionTests(TestCase):
    def test_bare_password_is_redacted(self):
        self.assertEqual(
            redact_secrets("password=hunter2"), "password=[REDACTED]"
        )

    def test_quoted_password_still_redacted(self):
        self.assertEqual(
            redact_secrets("password='hunter2'"), "password='[REDACTED]'"
        )
        self.assertEqual(
            redact_secrets('password="hunter2"'), 'password="[REDACTED]"'
        )

    def test_password_with_colon_and_trailing_delimiter(self):
        self.assertEqual(
            redact_secrets("login failed; password: hunter2; retry"),
            "login failed; password: [REDACTED]; retry",
        )

    def test_no_false_positive_on_plain_text(self):
        self.assertEqual(redact_secrets("plain text"), "plain text")


class LineNumberFingerprintTests(TestCase):
    def test_line_word_numbers_do_not_split_groups(self):
        base = (
            "Traceback (most recent call last):\n"
            '  File "/app/payments.py", line %d, in process_payment\n'
            "    charge(order)\n"
            '  File "/app/stripe.py", line 45, in charge\n'
            "    raise StripeError"
        )
        f1 = fingerprint(
            message="StripeError: card declined", stacktrace=base % 21
        )
        f2 = fingerprint(
            message="StripeError: card declined", stacktrace=base % 31
        )
        self.assertEqual(f1, f2)

    def test_module_entry_frames_do_not_split_groups(self):
        f1 = fingerprint(
            message="ValueError: boom",
            stacktrace='  File "/app/main.py", line 10, in <module>\n'
            '  File "/app/lib.py", line 5, in run\n'
            "    raise ValueError",
        )
        f2 = fingerprint(
            message="ValueError: boom",
            stacktrace='  File "/app/lib.py", line 5, in run\n'
            "    raise ValueError",
        )
        self.assertEqual(f1, f2)


class ErrorGroupListTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        register_and_login(self.client, "dev@trazeiq.io")
        self.project = create_project(self.client)
        self.ingest_headers = {"X-API-Key": self.project["api_key"]}

    def _ingest(self, message="ValueError: boom"):
        return APIClient().post(
            "/api/v1/events/",
            {"message": message, "stacktrace": "Traceback..."},
            format="json",
            headers=self.ingest_headers,
        )

    def test_requires_auth(self):
        response = APIClient().get("/api/v1/error-groups/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "NOT_AUTHENTICATED")

    def test_lists_groups_after_ingest(self):
        self.assertEqual(self._ingest().status_code, 201)
        response = self.client.get("/api/v1/error-groups/")
        self.assertEqual(response.status_code, 200)
        groups = response.data["data"]["error_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["title"], "ValueError: boom")
        self.assertEqual(groups[0]["count"], 1)

    def test_project_filter_and_invalid_uuid(self):
        self._ingest()
        response = self.client.get(
            "/api/v1/error-groups/?project=%s" % self.project["id"]
        )
        self.assertEqual(len(response.data["data"]["error_groups"]), 1)
        bad = self.client.get("/api/v1/error-groups/?project=nope")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data["error"]["code"], "VALIDATION_FAILED")

    def test_cross_org_groups_are_invisible(self):
        self._ingest()
        bob = APIClient()
        register_and_login(bob, "bob@example.io")
        response = bob.get("/api/v1/error-groups/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["error_groups"], [])
