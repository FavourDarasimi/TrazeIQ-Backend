"""
Production settings — PostgreSQL, DEBUG off.

Every variable here is read from the environment with no dev fallbacks:
if a required var is missing, the server fails fast with
ImproperlyConfigured instead of silently running insecure.
"""

from .base import *  # noqa: F401,F403

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

# Email — falls back to the console backend until a real SMTP provider is set.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="TrazeIQ <no-reply@trazeiq.dev>")

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
