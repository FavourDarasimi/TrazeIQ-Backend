"""Phase 4D: alert delivery — email, Slack (webhook + bot token), webhook.

DoD: a test alert sends a real email and a real Slack message containing the
incident's title, severity, and link. Email goes through Django's email
machinery (locmem backend here — the same call site SMTP uses in prod);
Slack goes through the mocked HTTP client with the exact payload.
"""

import json
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from apps.incidents.models import Incident

from ..dispatchers import dispatch
from ..models import AlertLog, AlertRule
from ..services import evaluate_incident
from .test_alert_rules import AlertSetupMixin


def _rule(project_id, *, channel, target, condition=None):
    return AlertRule.objects.create(
        project_id=project_id,
        name="Delivery test",
        condition=condition or {"severity": "critical"},
        channel=channel,
        target=target,
        cooldown_minutes=15,
    )


class EmailDispatchTests(AlertSetupMixin, TestCase):
    def setUp(self):
        # register_and_login sends OTP emails; clear so only the dispatch
        # email is counted.
        super().setUp()
        mail.outbox.clear()

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="TrazeIQ <no-reply@trazeiq.dev>",
    )
    def test_email_contains_title_severity_and_link(self):
        rule = _rule(
            self.project_id,
            channel="email",
            target="oncall@example.io",
        )
        dispatch(rule, Incident.objects.get(pk=self.incident_id))

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["oncall@example.io"])
        self.assertIn("boom", message.subject)
        self.assertIn("critical", message.body)
        self.assertIn("incident", message.body)
        self.assertIn("http://localhost:3000/incidents/", message.body)
        self.assertIn("AI analysis pending", message.body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        APP_BASE_URL="https://trazeiq.example.com",
    )
    def test_link_uses_app_base_url(self):
        rule = _rule(self.project_id, channel="email", target="oncall@example.io")
        dispatch(rule, Incident.objects.get(pk=self.incident_id))
        self.assertIn(
            "https://trazeiq.example.com/incidents/",
            mail.outbox[0].body,
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_evaluate_logs_dispatched_and_sends_email(self):
        rule = _rule(self.project_id, channel="email", target="oncall@example.io")
        evaluate_incident(Incident.objects.get(pk=self.incident_id))
        self.assertEqual(len(mail.outbox), 1)
        log = AlertLog.objects.get(rule=rule)
        self.assertEqual(log.status, AlertLog.Status.DISPATCHED)
        self.assertEqual(log.error, "")


class WebhookDispatchTests(AlertSetupMixin, TestCase):
    def test_webhook_posts_incident_summary(self):
        rule = _rule(self.project_id, channel="webhook", target="https://hooks.example/h")
        payload = {"sent": None}

        def fake_open(opener, request, timeout):
            payload["sent"] = json.loads(request.data)
            return _FakeResponse(b"ok")

        with mock.patch(
            "apps.alerts.dispatchers.urllib.request.build_opener",
            side_effect=lambda handler: _FakeOpener(fake_open),
        ):
            dispatch(rule, Incident.objects.get(pk=self.incident_id))

        sent = payload["sent"]
        self.assertEqual(sent["title"], "boom")
        self.assertEqual(sent["severity"], "critical")
        self.assertIn("incidents/", sent["link"])
        self.assertIsNone(sent["root_cause"])  # no analysis yet

    def test_failed_webhook_is_logged_not_raised(self):
        rule = _rule(self.project_id, channel="webhook", target="https://hooks.example/h")

        with mock.patch(
            "apps.alerts.dispatchers.urllib.request.build_opener",
            side_effect=OSError("connection refused"),
        ):
            dispatch_attempts = evaluate_incident(
                Incident.objects.get(pk=self.incident_id)
            )

        self.assertEqual(dispatch_attempts, 1)
        log = AlertLog.objects.get(rule=rule)
        self.assertEqual(log.status, AlertLog.Status.FAILED)
        self.assertIn("connection refused", log.error)

    def test_dispatch_blocks_redirects(self):
        # A validated public URL must not be able to redirect onto the
        # internal network — the NoRedirect handler turns any redirect into
        # an HTTPError, i.e. a DispatchError.
        from ..dispatchers import DispatchError

        rule = _rule(self.project_id, channel="webhook", target="https://hooks.example/h")
        with mock.patch(
            "apps.alerts.dispatchers.urllib.request.build_opener",
            side_effect=_redirect_opener,
        ):
            with self.assertRaises(DispatchError):
                dispatch(rule, Incident.objects.get(pk=self.incident_id))


class SlackDispatchTests(AlertSetupMixin, TestCase):
    def test_slack_webhook_target_posts_blocks(self):
        rule = _rule(
            self.project_id,
            channel="slack",
            target="https://hooks.slack.com/services/T/XXXX/YYYY",
        )
        payload = {"sent": None}

        def fake_open(opener, request, timeout):
            payload["sent"] = json.loads(request.data)
            return _FakeResponse(b"ok")

        with mock.patch(
            "apps.alerts.dispatchers.urllib.request.build_opener",
            side_effect=lambda handler: _FakeOpener(fake_open),
        ):
            dispatch(rule, Incident.objects.get(pk=self.incident_id))

        sent = payload["sent"]
        self.assertIn("blocks", sent)
        self.assertIn("*Critical incident:* boom", sent["text"])
        self.assertIn("incidents/", sent["text"])

    def test_slack_channel_requires_connected_workspace(self):
        from ..dispatchers import DispatchError

        rule = _rule(self.project_id, channel="slack", target="#alerts")
        with self.assertRaises(DispatchError):
            dispatch(rule, Incident.objects.get(pk=self.incident_id))

    def test_slack_channel_posts_with_token(self):
        from apps.integrations.models import SlackIntegration

        SlackIntegration.objects.create(
            organization_id=self.org_id, access_token="xoxb-secret"
        )
        rule = _rule(self.project_id, channel="slack", target="#alerts")
        sent = {"payload": None, "headers": None}

        def fake_post_slack_message(*, token, channel, payload, timeout):
            sent["payload"] = payload
            sent["headers"] = (token, channel)

        with mock.patch(
            "apps.integrations.slack.post_slack_message",
            side_effect=fake_post_slack_message,
        ):
            dispatch(rule, Incident.objects.get(pk=self.incident_id))

        token, channel = sent["headers"]
        self.assertEqual(token, "xoxb-secret")
        self.assertEqual(channel, "#alerts")
        self.assertIn("*Critical incident:* boom", sent["payload"]["text"])


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _size=None):
        return self._body


class _FakeOpener:
    """Stand-in for a urllib opener whose open() records the request."""

    def __init__(self, handler):
        self._handler = handler

    def open(self, request, timeout=None):
        return self._handler(self, request, timeout)


def _redirect_opener(handler):
    import urllib.error

    def fake_open(opener, request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 302, "redirect blocked", {}, _FakeResponse(b"")
        )

    return _FakeOpener(fake_open)