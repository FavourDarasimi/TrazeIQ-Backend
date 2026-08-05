"""
Project-level Celery tasks.

App-specific background jobs live in `apps/<app>/tasks.py` (autodiscovered);
this module holds cross-cutting infrastructure tasks like the worker liveness
probe. A task only ever calls a service — no business logic here.
"""

from celery import shared_task


@shared_task(name="trazeiq_backend.tasks.ping")
def ping() -> str:
    """Liveness probe — confirms a worker is consuming jobs and returning results."""
    return "pong"
