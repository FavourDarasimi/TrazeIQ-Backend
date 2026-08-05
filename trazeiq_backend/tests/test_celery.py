"""
Phase 2A — Celery + Redis wiring.

Covers the two things this subphase owns: the worker actually runs the ping
task end-to-end (eager mode, no broker needed in the suite), and the
`ai_analysis` queue exists with its conservative rate limit, so Phase 2B's
`analyze_incident` inherits both without touching settings.
"""

from django.test import SimpleTestCase

from trazeiq_backend.celery import app
from trazeiq_backend.tasks import ping


class PingTaskTests(SimpleTestCase):
    def test_ping_returns_pong(self):
        self.assertEqual(ping(), "pong")

    def test_ping_task_runs_through_celery_machinery(self):
        previous = app.conf.task_always_eager
        app.conf.task_always_eager = True
        try:
            result = ping.delay()
            self.assertEqual(result.state, "SUCCESS")
            self.assertEqual(result.result, "pong")
        finally:
            app.conf.task_always_eager = previous


class QueueConfigurationTests(SimpleTestCase):
    def test_ai_analysis_queue_is_declared(self):
        queue_names = {q.name for q in app.conf.task_queues}
        self.assertIn("ai_analysis", queue_names)

    def test_ai_app_tasks_route_to_ai_analysis_queue(self):
        route = app.amqp.router.route({}, "apps.ai.tasks.analyze_incident")
        self.assertEqual(route["queue"].name, "ai_analysis")

    def test_ai_analysis_queue_rate_limit_is_conservative(self):
        route = app.amqp.router.route({}, "apps.ai.tasks.analyze_incident")
        self.assertEqual(route["rate_limit"], "15/m")

    def test_ping_task_stays_on_default_queue(self):
        route = app.amqp.router.route({}, "trazeiq_backend.tasks.ping")
        self.assertEqual(route["queue"].name, "default")
