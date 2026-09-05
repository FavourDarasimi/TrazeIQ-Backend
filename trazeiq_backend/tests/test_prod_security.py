"""Phase 5D — production security posture.

Loads ``trazeiq_backend.settings.prod`` in isolation (patched environment,
fresh module import) and asserts the hardened values: no DEBUG, forced
HTTPS/HSTS, secure cookies, and fail-fast required CORS/CSRF origins.
``SimpleTestCase`` — no database needed.

The module is never imported at file top level: prod requires env vars that
are deliberately absent outside a real deployment, so every load happens
inside :func:`_load_prod` under a patched environment.
"""

import importlib
import os
import sys
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

_PROD_MODULE = "trazeiq_backend.settings.prod"

_BASE_ENV = {
    "DJANGO_SECRET_KEY": "test-only-not-a-real-secret",
    "DJANGO_ALLOWED_HOSTS": "api.example.com",
    "DJANGO_CORS_ALLOWED_ORIGINS": "https://app.example.com",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://app.example.com",
    "AUTH_COOKIE_SECURE": "True",
    "POSTGRES_DB": "trazeiq",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "test-only",
}


def _load_prod(extra=None):
    """Import (or reload) the prod settings module under a patched env."""
    env = dict(_BASE_ENV)
    env.update(extra or {})
    with mock.patch.dict(os.environ, env, clear=False):
        if _PROD_MODULE in sys.modules:
            return importlib.reload(sys.modules[_PROD_MODULE])
        return importlib.import_module(_PROD_MODULE)


class ProdSecurityTests(SimpleTestCase):
    def tearDown(self):
        # Restore module state so a reloaded prod module never leaks into
        # other tests (reload is a no-op for anyone not importing prod).
        if _PROD_MODULE in sys.modules:
            with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
                try:
                    importlib.reload(sys.modules[_PROD_MODULE])
                except ImproperlyConfigured:
                    sys.modules.pop(_PROD_MODULE, None)

    def test_debug_defaults_off(self):
        settings = _load_prod({"DJANGO_DEBUG": "False"})
        self.assertFalse(settings.DEBUG)

    def test_ssl_redirect_and_hsts_enabled(self):
        settings = _load_prod()
        self.assertTrue(settings.SECURE_SSL_REDIRECT)
        self.assertGreaterEqual(settings.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)

    def test_secure_cookies_forced(self):
        settings = _load_prod(
            {"SESSION_COOKIE_SECURE": "False", "CSRF_COOKIE_SECURE": "False"}
        )
        # Prod forces these regardless of environment overrides.
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertTrue(settings.AUTH_COOKIE_SECURE)

    def test_cors_and_csrf_origins_locked_to_env(self):
        settings = _load_prod(
            {
                "DJANGO_CORS_ALLOWED_ORIGINS": "https://app.example.com",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "https://api.example.com",
            }
        )
        self.assertEqual(
            settings.CORS_ALLOWED_ORIGINS, ["https://app.example.com"]
        )
        self.assertEqual(
            settings.CSRF_TRUSTED_ORIGINS, ["https://api.example.com"]
        )

    def test_missing_cors_origins_fails_fast(self):
        env = dict(_BASE_ENV)
        del env["DJANGO_CORS_ALLOWED_ORIGINS"]
        # The dev .env may supply this var via os.environ — scrub it so the
        # fail-fast path is genuinely exercised.
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                if _PROD_MODULE in sys.modules:
                    importlib.reload(sys.modules[_PROD_MODULE])
                else:
                    importlib.import_module(_PROD_MODULE)
        sys.modules.pop(_PROD_MODULE, None)

    def test_missing_csrf_origins_fails_fast(self):
        env = dict(_BASE_ENV)
        del env["DJANGO_CSRF_TRUSTED_ORIGINS"]
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                if _PROD_MODULE in sys.modules:
                    importlib.reload(sys.modules[_PROD_MODULE])
                else:
                    importlib.import_module(_PROD_MODULE)
        sys.modules.pop(_PROD_MODULE, None)

    def test_proxy_header_opt_in_only(self):
        settings = _load_prod()
        self.assertFalse(hasattr(settings, "SECURE_PROXY_SSL_HEADER"))
        settings = _load_prod({"DJANGO_BEHIND_PROXY": "True"})
        self.assertEqual(
            settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
        )
