"""
Development settings — SQLite for local dev, DEBUG on.

PostgreSQL is used in production (see prod.py). Anything secret comes from
the environment; never hardcode values here.
"""

from .base import *  # noqa: F401,F403

DEBUG = True

# Swagger/OpenAPI docs are on in dev by default; set DJANGO_ENABLE_API_SCHEMA=False to hide.
ENABLE_API_SCHEMA = env.bool("DJANGO_ENABLE_API_SCHEMA", default=True)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # Single-writer SQLite serializes concurrent writers instead of
        # queueing them like Postgres — a longer busy timeout keeps local
        # crash-loop demos from 500ing under threaded bursts. Prod uses
        # Postgres (see prod.py) and is unaffected.
        "OPTIONS": {"timeout": 30},
    }
}

# WAL mode narrows the SQLite-vs-Postgres gap further for local crash-loop
# demos: writers no longer upgrade-deadlock concurrent transactions the way
# rollback-journal mode does, so threaded bursts behave instead of 500ing.
from django.db.backends.signals import connection_created  # noqa: E402


def _enable_sqlite_wal(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")


connection_created.connect(
    _enable_sqlite_wal, dispatch_uid="trazeiq-dev-sqlite-wal"
)

# OTP delivery in dev goes to the console log — no SMTP required. AUTH_DEV_OTP
# (000000) is accepted for any user until real email delivery is configured.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "TrazeIQ <no-reply@trazeiq.dev>"
