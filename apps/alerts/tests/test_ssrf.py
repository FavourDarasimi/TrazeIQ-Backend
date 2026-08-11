"""Phase 4D: SSRF defense on alert targets.

DoD: a target pointing at a private IP (127.0.0.1, 169.254.169.254, 10.x.x.x)
is rejected at rule creation; hostnames resolving to private addresses are
rejected too; public targets pass.
"""

import socket
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.alerts.security import validate_dispatch_target_url

from .test_alert_rules import AlertSetupMixin

PRIVATE = "127.0.0.1"


class SsrfValidatorTests(TestCase):
    def assert_rejected(self, url):
        with self.assertRaises(ValidationError):
            validate_dispatch_target_url(url)

    def test_rejects_literal_private_addresses(self):
        for url in [
            "http://127.0.0.1/hook",
            "https://127.0.0.1/hook",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.1/hook",
            "http://192.168.1.1/hook",
            "http://172.16.0.1/hook",
            "http://0.0.0.0/hook",
            "http://[::1]/hook",
            "http://[fc00::1]/hook",
        ]:
            with self.subTest(url=url):
                self.assert_rejected(url)

    def test_rejects_non_http_schemes(self):
        for url in ["ftp://example.com/hook", "file:///etc/passwd", "mailto:x@y.z"]:
            with self.subTest(url=url):
                self.assert_rejected(url)

    def test_rejects_unresolvable_hostnames(self):
        with mock.patch("apps.alerts.security.socket.getaddrinfo", side_effect=socket.gaierror):
            self.assert_rejected("https://no-such-host.invalid/hook")

    def test_rejects_hostname_resolving_to_private_ip(self):
        # localtest.me-style aliases resolve to loopback — must be caught.
        with mock.patch(
            "apps.alerts.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PRIVATE, 80))],
        ):
            self.assert_rejected("https://localtest.me/hook")

    def test_rejects_hostname_resolving_any_private_ip(self):
        public, private = ("93.184.216.34", "10.0.0.5")
        with mock.patch(
            "apps.alerts.security.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (public, 80)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (private, 80)),
            ],
        ):
            self.assert_rejected("https://mixed.example/hook")

    def test_accepts_public_hostname(self):
        with mock.patch(
            "apps.alerts.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        ):
            url = validate_dispatch_target_url("https://example.com/hook")
        self.assertEqual(url, "https://example.com/hook")

    def test_accepts_literal_public_ip(self):
        url = validate_dispatch_target_url("https://93.184.216.34/hook")
        self.assertEqual(url, "https://93.184.216.34/hook")


class RuleTargetValidationTests(AlertSetupMixin):
    """The same defense at rule creation: fail fast with a clear message."""

    def _rule(self, channel, target, **kwargs):
        kwargs.setdefault("condition", {"severity": "critical"})
        return self.owner.post(
            "/api/v1/alerts/rules/",
            {
                "project": self.project_id,
                "name": "SSRF test",
                "condition": kwargs["condition"],
                "channel": channel,
                "target": target,
                "cooldown_minutes": 15,
            },
            format="json",
        )

    def test_webhook_target_to_private_ip_is_rejected(self):
        # Literal IPs are rejected without any DNS round-trip.
        response = self._rule("webhook", "http://127.0.0.1/hook")
        self.assertEqual(response.status_code, 400)
        self.assertIn("target", response.data["error"]["fields"])
        self.assertIn("private", response.data["error"]["fields"]["target"][0])

    def test_webhook_target_to_metadata_ip_is_rejected(self):
        response = self._rule("webhook", "http://169.254.169.254/latest/meta-data")
        self.assertEqual(response.status_code, 400)

    def test_webhook_target_to_private_hostname_is_rejected(self):
        with mock.patch(
            "apps.alerts.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))],
        ):
            response = self._rule("webhook", "https://internal.example/hook")
        self.assertEqual(response.status_code, 400)

    def test_webhook_target_to_public_url_is_accepted(self):
        with mock.patch(
            "apps.alerts.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        ):
            response = self._rule("webhook", "https://example.com/hook")
        self.assertEqual(response.status_code, 201)

    def test_slack_url_target_is_validated_but_channel_name_is_not(self):
        with mock.patch(
            "apps.alerts.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))],
        ):
            response = self._rule("slack", "https://hooks.internal/hook")
        self.assertEqual(response.status_code, 400)
        response = self._rule("slack", "#alerts")
        self.assertEqual(response.status_code, 201)

    def test_email_target_must_look_like_an_email(self):
        response = self._rule("email", "not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertIn("target", response.data["error"]["fields"])
        response = self._rule("email", "oncall@example.io")
        self.assertEqual(response.status_code, 201)

    def test_patch_cannot_introduce_private_target(self):
        with mock.patch(
            "apps.alerts.security.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        ):
            rule_id = self._rule("webhook", "https://example.com/hook").data["data"]["rule"]["id"]
        response = self.owner.patch(
            f"/api/v1/alerts/rules/{rule_id}/",
            {"target": "http://127.0.0.1/hook"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)