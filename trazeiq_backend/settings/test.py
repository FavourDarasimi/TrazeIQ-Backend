"""
Test settings — SQLite (dev database config) with third-party calls disabled.

Analysis and alert dispatch run inline (no worker), so an ambient
OPENROUTER_API_KEY from ``.env`` would make every ingesting test hit the
real model API. Blank it here: tests that exercise the model set their own
key via ``override_settings`` plus a mocked ``call_openrouter``.

Run the suite with::

    DJANGO_ENV=test venv/bin/python manage.py test
"""

from .dev import *  # noqa: F401,F403

# Disable external third-party calls during test runs
PUSHER_APP_ID = ""
PUSHER_KEY = ""
PUSHER_SECRET = ""
OPENROUTER_API_KEY = ""
