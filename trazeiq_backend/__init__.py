# Ensure the Celery app is loaded when Django starts so all tasks are
# registered and `.delay()` calls resolve against the real worker.
from .celery import app as celery_app

__all__ = ("celery_app",)
