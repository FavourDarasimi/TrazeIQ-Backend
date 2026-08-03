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
