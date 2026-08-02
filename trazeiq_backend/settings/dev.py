"""
Development settings — SQLite for local dev, DEBUG on.

PostgreSQL is used in production (see prod.py). Anything secret comes from
the environment; never hardcode values here.
"""

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
