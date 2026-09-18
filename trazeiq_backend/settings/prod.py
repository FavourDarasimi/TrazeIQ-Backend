"""
Production settings — PostgreSQL, DEBUG off.

Every variable here is read from the environment with no dev fallbacks:
if a required var is missing, the server fails fast with
ImproperlyConfigured instead of silently running insecure.
"""

from .base import *  # noqa: F401,F403

from django.core.exceptions import ImproperlyConfigured

DEBUG = env.bool("DJANGO_DEBUG", default=False)

# API docs stay off in production; opt in only if you really want to publish
# the OpenAPI schema (e.g. an internal API portal).
ENABLE_API_SCHEMA = env.bool("DJANGO_ENABLE_API_SCHEMA", default=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": 60,
    }
}

# Static files — compressed + content-hashed by WhiteNoise, served straight
# from STATIC_ROOT. Requires `collectstatic` at build/release (see DEPLOY.md).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Email — no silent fallback. The console/dummy backends never deliver mail:
# with one active, signup OTP codes, password resets and email alerts all
# report success while going nowhere. Prod refuses to boot until delivery
# is configured — set EMAIL_HOST (+ port/TLS/credentials) for SMTP, which
# also switches the backend off the dev default below.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="TrazeIQ <no-reply@trazeiq.dev>")

if "console" in EMAIL_BACKEND or "dummy" in EMAIL_BACKEND or (
    "smtp" in EMAIL_BACKEND.lower() and not EMAIL_HOST
):
    raise ImproperlyConfigured(
        "Email delivery is not configured: without it, signup OTP codes, "
        "password resets and email alerts silently go nowhere. Set "
        "EMAIL_HOST (plus EMAIL_PORT/EMAIL_USE_TLS/EMAIL_HOST_USER/"
        "EMAIL_HOST_PASSWORD as needed) for SMTP, or EMAIL_BACKEND to a "
        "delivering backend."
    )

AUTH_COOKIE_SECURE = env.bool("AUTH_COOKIE_SECURE", default=True)

# ---- Production hardening (Phase 5D) ----
# HTTPS everywhere: redirect plain HTTP to HTTPS and tell browsers to only
# ever use HTTPS for this host (HSTS, one year + subdomains by default).
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)

# Cookies: never send session/CSRF cookies over plain HTTP.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Only enable this behind a proxy/LB that terminates TLS and sets
# X-Forwarded-Proto — otherwise a client could spoof request.is_secure().
# When enabled, SECURE_SSL_REDIRECT trusts the forwarded proto header.
if env.bool("DJANGO_BEHIND_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# CORS/CSRF origins are required in prod — no localhost fallback. The deploy
# fails fast at boot if these are missing, rather than silently allowing the
# wrong origins (or none, which would break the cookie flows).
CORS_ALLOWED_ORIGINS = env.list("DJANGO_CORS_ALLOWED_ORIGINS")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Shared cache for throttles/rate limits. Use Redis when DJANGO_REDIS_URL is
# set (else fall back to per-process memory, which under-counts in multi-worker
# deployments).
if env("DJANGO_REDIS_URL", default=""):
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": env("DJANGO_REDIS_URL"),
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
