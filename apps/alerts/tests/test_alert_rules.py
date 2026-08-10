"""Phase 4C: alert rule engine tests.

DoD: a ``severity=critical`` rule + critical incident → exactly one dispatch
even with more occurrences in the cooldown window; the cooldown expiring re-
triggers a dispatch; evaluation runs async and never blocks ingestion.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from uuid import uuid4

from apps.events.tests.test_events import create_org, create_project, register_and_login
from apps.incidents.models import Incident
from apps.organizations.models import Membership

from ..models import AlertLog, AlertRule
from ..services import evaluate_incident
from ..tasks import evaluate_alerts_for_incident

User = get_user_model()


def _create_rule(client, project_id, *, condition, name="Critical pager",
                 channel="email", target="oncall@example.io", cooldown=15):
    return client.post(
        "/api/v1/alerts/rules/",
        {
            "project": project_id,
            "name": name,
            "condition": condition,
            "channel": channel,
            "target": target,
            "cooldown_minutes": cooldown,
        },
        format="json",
    )


class AlertSetupMixin(TestCase):
    """An org with a project + critical incident, plus owner/admin/developer/
    viewer clients."""

    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")
        self.project = create_project(self.owner, org=self.org_id)
        self.project_id = self.project["id"]

        self.ingest_client = APIClient()
        self.ingest_client.post(
            "/api/v1/events/",
            {"message": "boom", "level": "fatal"},
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

    def create_rule(self, client=None, **kwargs):
        kwargs.setdefault("condition", {"severity": "critical"})
        return _create_rule(client or self.owner, self.project_id, **kwargs)

    def add_event(self, level="fatal"):
        return self.ingest_client.post(
            "/api/v1/events/",
            {"message": "boom", "level": level},
            format="json",
            headers={"X-API-Key": self.project["api_key"]},
        )

    def _incident(self):
        return Incident.objects.get(pk=self.incident_id)


class AlertRuleCrudTests(AlertSetupMixin):
    def test_owner_creates_rule(self):
        response = self.create_rule(condition={"severity": "critical"})
        self.assertEqual(response.status_code, 201)
        rule = response.data["data"]["rule"]
        self.assertEqual(rule["condition"], {"severity": "critical"})
        self.assertEqual(rule["cooldown_minutes"], 15)
        self.assertEqual(rule["project"]["id"], self.project_id)
        self.assertEqual(rule["project"]["name"], "Web")

    def test_cooldown_default_is_15(self):
        response = self.create_rule()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["data"]["rule"]["cooldown_minutes"], 15)

    def test_list_and_filter_by_project(self):
        self.create_rule()
        other = create_project(self.owner, org=self.org_id, name="API")
        response = self.owner.get("/api/v1/alerts/rules/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]["rules"]), 1)
        response = self.owner.get("/api/v1/alerts/rules/", {"project": other["id"]})
        self.assertEqual(response.data["data"]["rules"], [])

    def test_patch_updates_rule(self):
        rule_id = self.create_rule().data["data"]["rule"]["id"]
        response = self.owner.patch(
            f"/api/v1/alerts/rules/{rule_id}/",
            {"condition": {"severity": "high"}, "cooldown_minutes": 5},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        rule = response.data["data"]["rule"]
        self.assertEqual(rule["condition"], {"severity": "high"})
        self.assertEqual(rule["cooldown_minutes"], 5)

    def test_rule_cannot_move_projects(self):
        rule_id = self.create_rule().data["data"]["rule"]["id"]
        other = create_project(self.owner, org=self.org_id, name="API")
        response = self.owner.patch(
            f"/api/v1/alerts/rules/{rule_id}/",
            {"project": other["id"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("project", response.data["error"]["fields"])

    def test_delete_removes_rule(self):
        rule_id = self.create_rule().data["data"]["rule"]["id"]
        response = self.owner.delete(f"/api/v1/alerts/rules/{rule_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AlertRule.objects.count(), 0)

    def test_nonexistent_rule_is_denied(self):
        # No such rule → the permission cannot resolve an org, so the write
        # is denied outright (same contract as the other permission-gated
        # endpoints); a *foreign* rule resolves an org and 404s instead.
        response = self.owner.patch(
            f"/api/v1/alerts/rules/{uuid4()}/", {"name": "x"}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        response = self.owner.delete(f"/api/v1/alerts/rules/{uuid4()}/")
        self.assertEqual(response.status_code, 403)

    def test_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.get("/api/v1/alerts/rules/")
        self.assertEqual(response.status_code, 401)


class ConditionValidationTests(AlertSetupMixin):
    def test_rejects_non_object_condition(self):
        for bad in ([], "critical", 3, None):
            response = self.create_rule(condition=bad)
            self.assertEqual(response.status_code, 400)
            self.assertIn("condition", response.data["error"]["fields"])

    def test_rejects_empty_condition(self):
        response = self.create_rule(condition={})
        self.assertEqual(response.status_code, 400)

    def test_rejects_unknown_keys(self):
        response = self.create_rule(condition={"severity": "critical", "foo": 1})
        self.assertEqual(response.status_code, 400)
        self.assertIn("foo", response.data["error"]["fields"]["condition"][0])

    def test_rejects_invalid_values(self):
        response = self.create_rule(condition={"severity": "huge"})
        self.assertEqual(response.status_code, 400)
        response = self.create_rule(condition={"status": "wat"})
        self.assertEqual(response.status_code, 400)

    def test_accepts_status_condition(self):
        response = self.create_rule(condition={"status": "open"})
        self.assertEqual(response.status_code, 201)


class AlertRbacTests(AlertSetupMixin):
    def assert_denied(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "PERMISSION_DENIED")

    def test_viewer_cannot_create_rule(self):
        self.assert_denied(self.create_rule(client=self.roles["viewer"]))

    def test_developer_cannot_create_rule(self):
        self.assert_denied(self.create_rule(client=self.roles["developer"]))

    def test_admin_can_create_rule(self):
        response = self.create_rule(client=self.roles["admin"])
        self.assertEqual(response.status_code, 201)

    def test_viewer_can_read_rules_and_logs(self):
        self.create_rule()
        response = self.roles["viewer"].get("/api/v1/alerts/rules/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]["rules"]), 1)
        response = self.roles["viewer"].get("/api/v1/alerts/logs/")
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_patch_or_delete_rule(self):
        rule_id = self.create_rule().data["data"]["rule"]["id"]
        self.assert_denied(
            self.roles["viewer"].patch(
                f"/api/v1/alerts/rules/{rule_id}/", {"name": "x"}, format="json"
            )
        )
        self.assert_denied(
            self.roles["viewer"].delete(f"/api/v1/alerts/rules/{rule_id}/")
        )

    def test_cross_org_rule_is_invisible(self):
        other = APIClient()
        register_and_login(other, "bob@example.io")
        create_project(other, name="BobApp")
        rule_id = self.create_rule().data["data"]["rule"]["id"]
        response = other.get("/api/v1/alerts/rules/")
        self.assertEqual(response.data["data"]["rules"], [])
        response = other.patch(
            f"/api/v1/alerts/rules/{rule_id}/", {"name": "x"}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        response = other.delete(f"/api/v1/alerts/rules/{rule_id}/")
        self.assertEqual(response.status_code, 404)


class EvaluationTests(AlertSetupMixin):
    def test_matching_rule_dispatches_exactly_once_within_cooldown(self):
        rule = AlertRule.objects.create(
            project_id=self.project_id,
            name="Critical pager",
            condition={"severity": "critical"},
            channel="email",
            target="oncall@example.io",
            cooldown_minutes=15,
        )
        incident = self._incident()
        self.assertEqual(incident.severity, "critical")

        for _ in range(10):
            self.add_event(level="fatal")
            evaluate_incident(incident)

        self.assertEqual(AlertLog.objects.count(), 1)
        self.assertEqual(AlertLog.objects.get().rule_id, rule.id)

    def test_non_matching_severity_is_ignored(self):
        AlertRule.objects.create(
            project_id=self.project_id,
            name="High only",
            condition={"severity": "high"},
            channel="email",
            target="oncall@example.io",
            cooldown_minutes=15,
        )
        evaluate_incident(self._incident())
        self.assertEqual(AlertLog.objects.count(), 0)

    def test_cooldown_expiry_retriggers_dispatch(self):
        rule = AlertRule.objects.create(
            project_id=self.project_id,
            name="Critical pager",
            condition={"severity": "critical"},
            channel="email",
            target="oncall@example.io",
            cooldown_minutes=15,
        )
        incident = self._incident()
        evaluate_incident(incident)
        self.assertEqual(AlertLog.objects.count(), 1)

        AlertLog.objects.update(
            dispatched_at=timezone.now() - timedelta(minutes=16)
        )
        evaluate_incident(incident)
        self.assertEqual(AlertLog.objects.count(), 2)
        self.assertEqual(
            AlertLog.objects.filter(rule=rule).count(), 2
        )

    def test_status_condition_matches(self):
        rule = AlertRule.objects.create(
            project_id=self.project_id,
            name="Reopen pager",
            condition={"status": "open", "severity": "critical"},
            channel="email",
            target="oncall@example.io",
            cooldown_minutes=15,
        )
        incident = self._incident()
        evaluate_incident(incident)
        self.assertEqual(AlertLog.objects.count(), 1)

        incident.status = Incident.Status.INVESTIGATING
        incident.save(update_fields=["status"])
        evaluate_incident(incident)
        self.assertEqual(AlertLog.objects.count(), 1)  # no match now

    def test_every_rule_gets_its_own_log(self):
        for name in ("A", "B"):
            AlertRule.objects.create(
                project_id=self.project_id,
                name=name,
                condition={"severity": "critical"},
                channel="email",
                target=f"{name}@example.io",
                cooldown_minutes=15,
            )
        evaluate_incident(self._incident())
        self.assertEqual(AlertLog.objects.count(), 2)

    def test_task_uses_service_and_tolerates_missing_incident(self):
        evaluate_alerts_for_incident(str(self.incident_id))
        evaluate_alerts_for_incident(str(uuid4()))  # no crash


class IngestionTriggerTests(AlertSetupMixin):
    def test_broker_outage_never_blocks_ingestion(self):
        response = self.add_event(level="fatal")
        self.assertEqual(response.status_code, 201)

        with mock.patch(
            "apps.alerts.tasks.evaluate_alerts_for_incident.delay",
            side_effect=Exception("broker down"),
        ):
            response = self.add_event(level="fatal")
        self.assertEqual(response.status_code, 201)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
    @mock.patch("apps.events.services.enqueue_analysis_if_needed")
    def test_ingestion_eagerly_evaluates_and_dispatches(self, _analysis):
        self.create_rule(condition={"severity": "critical"})
        response = self.add_event(level="fatal")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(AlertLog.objects.count(), 1)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
    def test_patch_trigger_evaluates_rules(self):
        self.create_rule(condition={"severity": "high"})
        Incident.objects.filter(pk=self.incident_id).update(severity="medium")
        response = self.owner.patch(
            f"/api/v1/incidents/{self.incident_id}/",
            {"severity": "high"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AlertLog.objects.count(), 1)


class AlertLogViewTests(AlertSetupMixin):
    def test_log_routes_and_filters(self):
        rule = AlertRule.objects.create(
            project_id=self.project_id,
            name="Critical pager",
            condition={"severity": "critical"},
            channel="email",
            target="oncall@example.io",
            cooldown_minutes=15,
        )
        evaluate_incident(self._incident())

        response = self.owner.get("/api/v1/alerts/logs/")
        self.assertEqual(response.status_code, 200)
        logs = response.data["data"]["logs"]
        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log["rule"]["id"], str(rule.id))
        self.assertEqual(log["rule"]["channel"], "email")
        self.assertEqual(log["incident"]["severity"], "critical")
        self.assertEqual(log["incident"]["title"], "boom")

        response = self.owner.get("/api/v1/alerts/logs/", {"rule": uuid4()})
        self.assertEqual(response.data["data"]["logs"], [])
        response = self.owner.get(
            "/api/v1/alerts/logs/", {"incident": uuid4()}
        )
        self.assertEqual(response.data["data"]["logs"], [])

    def test_cross_org_logs_are_invisible(self):
        other = APIClient()
        register_and_login(other, "bob@example.io")
        create_project(other, name="BobApp")
        response = other.get("/api/v1/alerts/logs/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["logs"], [])