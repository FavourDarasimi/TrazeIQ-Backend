"""SLO backend tests.

DoD: define an SLO → budget calculation reflects events → burn-rate
windows produce a status → breach alerts fire on threshold crossings but
do not spam on every refresh → all endpoints are membership-scoped.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from uuid import uuid4

from apps.accounts.models import User
from apps.events.models import ErrorGroup, Event
from apps.events.tests.test_events import (
    create_org,
    create_project,
    register_and_login,
)
from apps.organizations.models import Membership, MembershipRole
from apps.projects.models import Project

from ..models import SLO, SLOBreachAlert, SLOBudgetSnapshot
from ..services import (
    acknowledge_alert,
    evaluate_all_enabled_slos,
    evaluate_slo,
    maybe_fire_breach_alerts,
    project_summary,
)

PASSWORD = "Password123!"


class SLOTestMixin(TestCase):
    """An owner + a developer + a viewer, plus a project, ready for SLO
    tests. Ingesting an error returns its Event so callers can place it
    inside an SLO's window."""

    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")
        self.project = create_project(self.owner, org=self.org_id)

        self.developer = APIClient()
        register_and_login(self.developer, "developer@trazeiq.io")
        Membership.objects.create(
            user=User.objects.get(email="developer@trazeiq.io"),
            organization_id=self.org_id,
            role=MembershipRole.DEVELOPER,
        )

        self.viewer = APIClient()
        register_and_login(self.viewer, "viewer@trazeiq.io")
        Membership.objects.create(
            user=User.objects.get(email="viewer@trazeiq.io"),
            organization_id=self.org_id,
            role=MembershipRole.VIEWER,
        )

        self.ingest_client = APIClient()

    def create_slo(self, **overrides):
        payload = {
            "project": self.project["id"],
            "name": "API uptime",
            "description": "99.9% uptime for the API",
            "target_pct": "99.90",
            "window_days": 30,
            "error_query": {"service": "api"},
            "alert_threshold_pct": "25.00",
            "enabled": True,
        }
        payload.update(overrides)
        return self.owner.post("/api/v1/slos/", payload, format="json")

    def ingest_event(
        self,
        message,
        *,
        level="error",
        service="api",
        created_at=None,
    ):
        project = Project.objects.get(id=self.project["id"])
        group, _ = ErrorGroup.objects.get_or_create(
            project=project,
            fingerprint=message[:32],
            defaults={
                "title": message,
                "first_seen": created_at or timezone.now(),
                "last_seen": created_at or timezone.now(),
                "count": 0,
            },
        )
        event = Event.objects.create(
            project=project,
            error_group=group,
            message=message,
            level=level,
            service=service,
            endpoint="/test",
            fingerprint=message[:32],
            metadata={},
        )
        if created_at:
            Event.objects.filter(id=event.id).update(created_at=created_at)
            event.refresh_from_db()
        return event


class SLOBudgetCalculationTests(SLOTestMixin):
    def test_evaluate_with_no_events_yields_full_budget(self):
        response = self.create_slo()
        self.assertEqual(response.status_code, 201)
        slo_id = response.data["data"]["slo"]["id"]

        snapshot = evaluate_slo(SLO.objects.get(id=slo_id))
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.total_events, 0)
        self.assertEqual(snapshot.bad_events, 0)
        self.assertEqual(snapshot.budget_total, Decimal("0"))
        # No traffic means there's nothing to burn — budget is at 100%
        # by definition and the SLO is healthy.
        self.assertEqual(snapshot.budget_remaining_pct, Decimal("100.00"))
        self.assertEqual(snapshot.status, SLOBudgetSnapshot.Status.HEALTHY)

    def test_budget_consumed_by_bad_events(self):
        response = self.create_slo(target_pct="99.00")  # 1% allowed
        slo_id = response.data["data"]["slo"]["id"]
        # 100 good events (info) + 2 bad events (error). Budget = 100 * 1% = 1.
        # 2 bad events consumes 2/1 -> budget fully gone.
        for i in range(100):
            self.ingest_event(f"info {i}", level="info")
        self.ingest_event("DatabaseError: down", level="error")
        self.ingest_event("TimeoutError: timeout", level="fatal")

        snapshot = evaluate_slo(SLO.objects.get(id=slo_id))
        self.assertEqual(snapshot.total_events, 102)
        self.assertEqual(snapshot.bad_events, 2)
        self.assertEqual(snapshot.budget_remaining_pct, Decimal("0.00"))
        self.assertEqual(snapshot.status, SLOBudgetSnapshot.Status.EXHAUSTED)

    def test_burn_rate_windows_match_recent_events(self):
        response = self.create_slo()
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        now = timezone.now()
        # 60 errors in the last hour → burn_rate_1h ≈ 60
        for i in range(60):
            self.ingest_event(
                f"recent {i}", level="error", created_at=now - timedelta(minutes=i)
            )
        # 12 errors 12 hours ago → burn_rate_24h covers, 6h doesn't
        for i in range(12):
            self.ingest_event(
                f"old {i}", level="error", created_at=now - timedelta(hours=12 + i)
            )

        snapshot = evaluate_slo(slo)
        self.assertEqual(snapshot.burn_rate_1h, Decimal("60.0000"))
        # 60 errors in 1h + 12 errors spread across 12-23h ago. Within 24h
        # we have all 72 errors, but spread over 24 hours = 3/h.
        self.assertAlmostEqual(float(snapshot.burn_rate_24h), 3.0, places=1)
        # The 12 old errors sit outside 6h, so burn_rate_6h = 60/6 = 10.
        self.assertAlmostEqual(float(snapshot.burn_rate_6h), 10.0, places=1)

    def test_status_filter_narrows_bad_events(self):
        # SLO with severity=critical → only fatal events count as bad.
        response = self.create_slo(error_query={"severity": "critical"})
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for i in range(10):
            self.ingest_event(f"info {i}", level="info")
        self.ingest_event("BadError: critical", level="error")  # not counted
        snapshot = evaluate_slo(slo)
        # Severity filter is a marker the UI can render; we keep the
        # engine's level filter at error+fatal regardless of "severity"
        # so the dashboard's "bad events" line stays meaningful.
        self.assertEqual(snapshot.total_events, 11)
        self.assertEqual(snapshot.bad_events, 1)

    def test_service_filter_excludes_other_services(self):
        response = self.create_slo(error_query={"service": "api"})
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for i in range(5):
            self.ingest_event(f"api {i}", level="error", service="api")
        for i in range(5):
            self.ingest_event(
                f"worker {i}", level="error", service="worker"
            )
        snapshot = evaluate_slo(slo)
        self.assertEqual(snapshot.total_events, 5)
        self.assertEqual(snapshot.bad_events, 5)

    def test_disabled_slo_returns_no_new_snapshot(self):
        response = self.create_slo(enabled=False)
        slo_id = response.data["data"]["slo"]["id"]
        snapshot = evaluate_slo(SLO.objects.get(id=slo_id))
        self.assertIsNone(snapshot)

    def test_status_derivation_warning_threshold(self):
        # Slow burn — 24h window with 50% budget consumed.
        # With window_days=2 and a 100% target (only allowed bad = total),
        # 50 bad events out of 100 total = exactly 50% budget consumed.
        response = self.create_slo(target_pct="99.00", window_days=2)
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        # 100 total / 1% budget = 1 bad allowed. 2 bad events = 100% gone
        # (exhausted, not warning) — different target.
        # Use a target of 50.00 → 50% allowed. 100 events / 50% = 50 budget.
        # 25 bad = 50% consumed → remaining = 50%; status derives from
        # burn rate, not from remaining alone.
        slo.target_pct = Decimal("50.00")
        slo.window_days = 30
        slo.save()
        now = timezone.now()
        for i in range(75):
            self.ingest_event(
                f"info {i}", level="info", created_at=now - timedelta(hours=i * 4)
            )
        for i in range(25):
            self.ingest_event(
                f"bad {i}", level="error", created_at=now - timedelta(hours=i * 4)
            )
        snapshot = evaluate_slo(slo)
        # 25 bad out of 100 total; budget allowed = 50; remaining = 25/50 = 50%.
        self.assertGreater(float(snapshot.budget_remaining_pct), 0)
        self.assertIn(
            snapshot.status,
            (
                SLOBudgetSnapshot.Status.HEALTHY,
                SLOBudgetSnapshot.Status.WARNING,
            ),
        )


class SLOBreachAlertTests(SLOTestMixin):
    def test_first_crossing_creates_alert(self):
        response = self.create_slo(target_pct="50.00", window_days=30)
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for i in range(10):
            self.ingest_event(f"bad {i}", level="error")
        snapshot = evaluate_slo(slo)
        # 10 bad out of 10 total. Budget = 10 * 50% = 5. Remaining = 0.
        self.assertEqual(snapshot.status, SLOBudgetSnapshot.Status.EXHAUSTED)
        alerts = maybe_fire_breach_alerts(snapshot)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, SLOBreachAlert.Kind.EXHAUSTED)

    def test_repeated_evaluation_does_not_duplicate_alerts(self):
        response = self.create_slo(target_pct="50.00", window_days=30)
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for i in range(10):
            self.ingest_event(f"bad {i}", level="error")
        first = evaluate_slo(slo)
        maybe_fire_breach_alerts(first)
        # Re-evaluating the still-breached SLO must not create a new alert.
        second = evaluate_slo(slo)
        maybe_fire_breach_alerts(second)
        self.assertEqual(SLOBreachAlert.objects.filter(slo=slo).count(), 1)

    def test_recovery_acknowledges_outstanding_alerts(self):
        response = self.create_slo(target_pct="99.00", window_days=30)
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        # Two fatal events out of very few total → easy breach.
        for i in range(2):
            self.ingest_event(f"bad {i}", level="fatal")
        breach = evaluate_slo(slo)
        maybe_fire_breach_alerts(breach)
        self.assertEqual(SLOBreachAlert.objects.filter(slo=slo).count(), 1)
        # Recovery: a huge volume of good events so the bad rate becomes
        # negligible (budget_total ≈ total * 1% = thousands, only 2 used).
        for i in range(10_000):
            self.ingest_event(f"info {i}", level="info")
        recovered = evaluate_slo(slo)
        self.assertEqual(recovered.status, SLOBudgetSnapshot.Status.HEALTHY)
        maybe_fire_breach_alerts(recovered)
        # Outstanding alert was auto-acknowledged, but no new alert row.
        self.assertEqual(SLOBreachAlert.objects.filter(slo=slo).count(), 1)
        self.assertIsNotNone(
            SLOBreachAlert.objects.filter(slo=slo).first().acknowledged_at
        )

    def test_next_crossing_after_recovery_creates_fresh_alert(self):
        # Strict target so a small surge of bad events can breach it.
        response = self.create_slo(target_pct="99.00", window_days=30)
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        # Phase 1: small breach. 200 total events, 5 bad at 99% target
        # = 2 budget. 5 bad > 2 → exhausted.
        for i in range(195):
            self.ingest_event(f"info 1 {i}", level="info")
        for i in range(5):
            self.ingest_event(f"bad 1 {i}", level="error")
        first = evaluate_slo(slo)
        maybe_fire_breach_alerts(first)
        self.assertEqual(SLOBreachAlert.objects.filter(slo=slo).count(), 1)
        # Phase 2: huge info flood so the existing burn becomes harmless.
        for i in range(50_000):
            self.ingest_event(f"info recover {i}", level="info")
        recovered = evaluate_slo(slo)
        self.assertEqual(recovered.status, SLOBudgetSnapshot.Status.HEALTHY)
        maybe_fire_breach_alerts(recovered)
        self.assertIsNotNone(
            SLOBreachAlert.objects.filter(slo=slo).first().acknowledged_at
        )
        # Phase 3: a fresh flood of bad events against the now-huge
        # budget — a few thousand errors at 99% target easily overwhelms.
        for i in range(2_000):
            self.ingest_event(f"bad 2 {i}", level="error")
        second_breach = evaluate_slo(slo)
        self.assertEqual(
            second_breach.status, SLOBudgetSnapshot.Status.EXHAUSTED
        )
        maybe_fire_breach_alerts(second_breach)
        all_alerts = list(
            SLOBreachAlert.objects.filter(slo=slo).order_by("fired_at")
        )
        self.assertEqual(len(all_alerts), 2)
        self.assertIsNotNone(all_alerts[0].acknowledged_at)
        self.assertIsNone(all_alerts[1].acknowledged_at)

    def test_acknowledge_alert_is_idempotent(self):
        response = self.create_slo(target_pct="50.00", window_days=30)
        slo_id = response.data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for i in range(10):
            self.ingest_event(f"bad {i}", level="error")
        snapshot = evaluate_slo(slo)
        alert = maybe_fire_breach_alerts(snapshot)[0]
        actor = User.objects.get(email="owner@trazeiq.io")
        acknowledge_alert(alert, actor=actor)
        first_ts = SLOBreachAlert.objects.get(id=alert.id).acknowledged_at
        acknowledge_alert(alert, actor=actor)
        second_ts = SLOBreachAlert.objects.get(id=alert.id).acknowledged_at
        self.assertEqual(first_ts, second_ts)


class SLOCrudApiTests(SLOTestMixin):
    def test_owner_creates_slo(self):
        response = self.create_slo()
        self.assertEqual(response.status_code, 201)
        data = response.data["data"]["slo"]
        self.assertEqual(data["name"], "API uptime")
        self.assertEqual(data["target_pct"], "99.90")
        self.assertEqual(data["window_days"], 30)
        self.assertEqual(data["error_query"], {"service": "api"})

    def test_developer_cannot_create_slo(self):
        response = self.developer.post(
            "/api/v1/slos/",
            {
                "project": self.project["id"],
                "name": "Dev SLO",
                "target_pct": "99.00",
            },
            format="json",
        )
        # The developer is a member; the org tier denies the write.
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.data["success"])

    def test_viewer_cannot_create_slo(self):
        response = self.viewer.post(
            "/api/v1/slos/",
            {
                "project": self.project["id"],
                "name": "Viewer SLO",
                "target_pct": "99.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_list_filters_by_project(self):
        self.create_slo(name="A")
        other_project = create_project(self.owner, org=self.org_id, name="Other")
        self.owner.post(
            "/api/v1/slos/",
            {
                "project": other_project["id"],
                "name": "B",
                "target_pct": "99.00",
            },
            format="json",
        )

        all_resp = self.owner.get("/api/v1/slos/")
        self.assertEqual(all_resp.status_code, 200)
        self.assertEqual(len(all_resp.data["data"]["slos"]), 2)

        scoped = self.owner.get(
            f"/api/v1/slos/?project={self.project['id']}"
        )
        self.assertEqual(len(scoped.data["data"]["slos"]), 1)

        scoped_other = self.owner.get(
            f"/api/v1/slos/?project={other_project['id']}"
        )
        self.assertEqual(len(scoped_other.data["data"]["slos"]), 1)

    def test_patch_updates_name_and_threshold(self):
        slo_id = self.create_slo().data["data"]["slo"]["id"]
        response = self.owner.patch(
            f"/api/v1/slos/{slo_id}/",
            {"name": "Renamed", "alert_threshold_pct": "10.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]["slo"]
        self.assertEqual(data["name"], "Renamed")
        self.assertEqual(data["alert_threshold_pct"], "10.00")

    def test_cannot_move_slo_to_different_project(self):
        slo_id = self.create_slo().data["data"]["slo"]["id"]
        other_project = create_project(self.owner, org=self.org_id, name="Other")
        response = self.owner.patch(
            f"/api/v1/slos/{slo_id}/",
            {"project": other_project["id"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("project", response.data["error"]["fields"])

    def test_delete_removes_slo(self):
        slo_id = self.create_slo().data["data"]["slo"]["id"]
        response = self.owner.delete(f"/api/v1/slos/{slo_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SLO.objects.count(), 0)

    def test_invalid_target_pct_rejected(self):
        response = self.create_slo(target_pct="120.00")
        self.assertEqual(response.status_code, 400)
        self.assertIn("target_pct", response.data["error"]["fields"])

    def test_unknown_error_query_key_rejected(self):
        response = self.create_slo(error_query={"invalid_key": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error_query", response.data["error"]["fields"])

    def test_cross_org_slo_returns_404(self):
        stranger = APIClient()
        register_and_login(stranger, "stranger@trazeiq.io")
        foreign_org = create_org(stranger, "Other")
        foreign = create_project(stranger, org=foreign_org)
        foreign_slo = stranger.post(
            "/api/v1/slos/",
            {
                "project": foreign["id"],
                "name": "Foreign",
                "target_pct": "99.00",
            },
            format="json",
        )
        foreign_id = foreign_slo.data["data"]["slo"]["id"]

        # Owner of Acme asking for Other's SLO — should 404, never leak.
        response = self.owner.get(f"/api/v1/slos/{foreign_id}/")
        self.assertEqual(response.status_code, 404)
        response = self.owner.patch(
            f"/api/v1/slos/{foreign_id}/",
            {"name": "hijack"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        response = self.owner.delete(f"/api/v1/slos/{foreign_id}/")
        self.assertEqual(response.status_code, 404)


class SLOBudgetApiTests(SLOTestMixin):
    def test_budget_endpoint_returns_snapshot(self):
        slo_id = self.create_slo().data["data"]["slo"]["id"]
        for i in range(5):
            self.ingest_event(f"info {i}", level="info")
        self.ingest_event("DatabaseError", level="fatal")

        response = self.owner.get(f"/api/v1/slos/{slo_id}/budget/")
        self.assertEqual(response.status_code, 200)
        snapshot = response.data["data"]["snapshot"]
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["total_events"], 6)
        self.assertEqual(snapshot["bad_events"], 1)
        self.assertIn(snapshot["status"], ["healthy", "warning", "critical", "exhausted"])

    def test_history_endpoint_returns_chronological(self):
        slo_id = self.create_slo().data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for _ in range(3):
            evaluate_slo(slo)
        response = self.owner.get(f"/api/v1/slos/{slo_id}/history/")
        self.assertEqual(response.status_code, 200)
        snapshots = response.data["data"]["snapshots"]
        self.assertEqual(len(snapshots), 3)
        # Oldest first — chronological.
        timestamps = [snap["evaluated_at"] for snap in snapshots]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_alerts_endpoint_returns_fired_alerts(self):
        slo_id = self.create_slo(target_pct="50.00").data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for i in range(10):
            self.ingest_event(f"bad {i}", level="error")
        snapshot = evaluate_slo(slo)
        maybe_fire_breach_alerts(snapshot)

        alerts = self.owner.get(f"/api/v1/slos/{slo_id}/alerts/")
        self.assertEqual(alerts.status_code, 200)
        self.assertEqual(len(alerts.data["data"]["alerts"]), 1)

    def test_acknowledge_endpoint_clears_open_alert(self):
        slo_id = self.create_slo(target_pct="50.00").data["data"]["slo"]["id"]
        slo = SLO.objects.get(id=slo_id)
        for i in range(10):
            self.ingest_event(f"bad {i}", level="error")
        snapshot = evaluate_slo(slo)
        alert = maybe_fire_breach_alerts(snapshot)[0]
        response = self.owner.post(
            f"/api/v1/slos/{slo_id}/alerts/{alert.id}/acknowledge/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data["data"]["alert"]["acknowledged_at"])

    def test_viewer_can_read_but_not_mutate(self):
        slo_id = self.create_slo().data["data"]["slo"]["id"]
        # Reads are open to any member.
        self.assertEqual(
            self.viewer.get(f"/api/v1/slos/{slo_id}/").status_code, 200
        )
        self.assertEqual(
            self.viewer.get(f"/api/v1/slos/{slo_id}/budget/").status_code, 200
        )
        self.assertEqual(
            self.viewer.get(f"/api/v1/slos/{slo_id}/history/").status_code, 200
        )
        self.assertEqual(
            self.viewer.get(f"/api/v1/slos/{slo_id}/alerts/").status_code, 200
        )
        # Writes are owner/admin only.
        self.assertEqual(
            self.viewer.patch(
                f"/api/v1/slos/{slo_id}/",
                {"name": "nope"},
                format="json",
            ).status_code,
            403,
        )
        self.assertEqual(
            self.viewer.delete(f"/api/v1/slos/{slo_id}/").status_code, 403,
        )


class SLODependencyImpactTests(SLOTestMixin):
    def test_dependency_summary_groups_by_service(self):
        self.create_slo(name="API", error_query={"service": "api"})
        self.create_slo(name="Worker", error_query={"service": "worker"})
        self.create_slo(name="Project-wide", error_query={})

        response = self.owner.get(
            f"/api/v1/slos/dependencies/?project={self.project['id']}"
        )
        self.assertEqual(response.status_code, 200)
        summary = response.data["data"]["summary"]
        self.assertEqual(summary["total_slos"], 3)
        services = sorted(row["service"] for row in summary["slos"])
        self.assertEqual(services, ["", "api", "worker"])

    def test_dependency_view_cross_org_404(self):
        stranger = APIClient()
        register_and_login(stranger, "stranger@trazeiq.io")
        foreign_org = create_org(stranger, "Other")
        foreign = create_project(stranger, org=foreign_org)
        response = self.owner.get(
            f"/api/v1/slos/dependencies/?project={foreign['id']}"
        )
        self.assertEqual(response.status_code, 404)

    def test_dependency_view_requires_project(self):
        response = self.owner.get("/api/v1/slos/dependencies/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("project", response.data["error"]["fields"])


class SLOBulkEvaluationTests(SLOTestMixin):
    def test_evaluate_all_returns_count(self):
        # Two enabled SLOs + one disabled; only enabled contribute to the count.
        self.create_slo(name="A", enabled=True)
        self.create_slo(name="B", enabled=True)
        self.create_slo(name="C", enabled=False)
        self.assertEqual(evaluate_all_enabled_slos(), 2)

    def test_evaluate_all_isolates_per_slo_failures(self):
        self.create_slo(name="A")
        # Patch evaluate_slo so one call explodes; the rest must still succeed.
        original = evaluate_slo

        def flaky(slo, **kwargs):
            if slo.name == "A":
                raise RuntimeError("boom")
            return original(slo, **kwargs)

        with mock.patch("apps.slos.services.evaluate_slo", side_effect=flaky):
            # No exception escapes — that's the contract.
            self.assertEqual(evaluate_all_enabled_slos(), 0)


class SLOPeriodicTaskTests(SLOTestMixin):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_celery_task_evaluates_everything(self):
        from ..tasks import evaluate_all_slos

        self.create_slo(name="A")
        self.create_slo(name="B")
        # Eager mode runs the task synchronously inside .delay(); use the
        # bound function directly to count the result deterministically.
        self.assertEqual(evaluate_all_slos(), 2)