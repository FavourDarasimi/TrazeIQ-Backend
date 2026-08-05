"""
Celery application entrypoint for trazeiq_backend.

The worker reads its configuration (broker URL, result backend, queues, task
routes) from Django's CELERY_* settings, so everything is tunable via .env.

Run a worker locally:

    celery -A trazeiq_backend worker -l info --pool=solo

(--pool=solo is required on Windows, where Celery's default prefork pool is
not supported. On Linux/macOS plain `celery -A trazeiq_backend worker -l info`
is fine.)

Three processes make up the async path: this worker, Redis (the broker), and
Django. `docker-compose up` from the repo root runs all three together.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trazeiq_backend.settings")

app = Celery("trazeiq_backend", include=["trazeiq_backend.tasks"])
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

if __name__ == "__main__":
    app.start()
