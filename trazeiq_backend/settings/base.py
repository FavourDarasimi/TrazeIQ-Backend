"""
Shared settings for the trazeiq_backend project.

Every environment (dev/prod) inherits from this module. Secrets are read
from the environment via django-environ — never hardcode them here.
The .env file (gitignored) is loaded at import time; .env.example documents
every variable this project can read.
"""

from pathlib import Path

import environ

# server/ — one level up from the settings package
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# Security
SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-do-not-use-in-prod")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# HMAC key for API-key digests (projects). Falls back to SECRET_KEY when unset —
# set it explicitly once projects exist in more than one environment.
API_KEY_HASH_SECRET = env("API_KEY_HASH_SECRET", default="")

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "axes",
    "apps.accounts",
    "apps.organizations",
    "apps.projects",
    "apps.events",
    "apps.incidents",
    "apps.ai",
    "apps.alerts",
    "apps.integrations",
    "apps.notifications",
    "apps.analytics",
    "apps.realtime",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "trazeiq_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "trazeiq_backend.wsgi.application"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom user model — email is the login identifier
AUTH_USER_MODEL = "accounts.User"

# ---- Auth / OTP ----
# Refresh token lives in this httpOnly cookie; the access token is returned in
# the response body AND mirrored into its own httpOnly cookie (sameSite=Lax),
# so requests without an Authorization header still authenticate.
AUTH_COOKIE_NAME = "trazeiq_refresh"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
AUTH_COOKIE_SECURE = env.bool("AUTH_COOKIE_SECURE", default=False)
AUTH_COOKIE_SAMESITE = env("AUTH_COOKIE_SAMESITE", default="Lax")

AUTH_ACCESS_COOKIE_NAME = "trazeiq_access"
AUTH_ACCESS_COOKIE_MAX_AGE = 15 * 60  # matches ACCESS_TOKEN_LIFETIME

# When AUTH_DEV_OTP is set (e.g. "000000"), OTP verification accepts this code
# for any user — dev/demo bypass until real email delivery is configured.
AUTH_DEV_OTP = env("AUTH_DEV_OTP", default="")
AUTH_OTP_TTL_MINUTES = env.int("AUTH_OTP_TTL_MINUTES", default=10)
AUTH_OTP_MAX_ATTEMPTS = env.int("AUTH_OTP_MAX_ATTEMPTS", default=5)

# Single-use registration token issued by register/verify-otp and consumed by
# register/complete (OTP-first signup). Only its hash is stored.
AUTH_REGISTRATION_TOKEN_TTL_MINUTES = env.int(
    "AUTH_REGISTRATION_TOKEN_TTL_MINUTES", default=15
)
# Hard per-email ceiling for signup codes, on top of the per-IP throttle.
AUTH_REGISTER_EMAIL_CAP = env.int("AUTH_REGISTER_EMAIL_CAP", default=3)
AUTH_REGISTER_EMAIL_CAP_MINUTES = env.int(
    "AUTH_REGISTER_EMAIL_CAP_MINUTES", default=15
)

# ---- Per-endpoint rate limits (auth) ----
# Each auth endpoint is throttled per client IP. Rates are Django throttle
# strings ("N/min", "N/hour", ...) and are read by AuthScopedRateThrottle
# from these settings so environments can tune them via .env.
AUTH_THROTTLE_LOGIN = env("AUTH_THROTTLE_LOGIN", default="60/min")
AUTH_THROTTLE_REGISTER_REQUEST = env(
    "AUTH_THROTTLE_REGISTER_REQUEST", default="10/min"
)
AUTH_THROTTLE_REGISTER_VERIFY = env(
    "AUTH_THROTTLE_REGISTER_VERIFY", default="30/min"
)
AUTH_THROTTLE_REGISTER_COMPLETE = env(
    "AUTH_THROTTLE_REGISTER_COMPLETE", default="20/min"
)
AUTH_THROTTLE_FORGOT = env("AUTH_THROTTLE_FORGOT", default="10/min")
AUTH_THROTTLE_RESET = env("AUTH_THROTTLE_RESET", default="20/min")
AUTH_THROTTLE_GOOGLE = env("AUTH_THROTTLE_GOOGLE", default="20/min")
AUTH_THROTTLE_REFRESH = env("AUTH_THROTTLE_REFRESH", default="30/min")

# ---- Event ingestion ----
# Cap on the message+stacktrace payload accepted by POST /api/v1/events/.
# Oversized payloads get a 413 before anything else happens.
EVENT_MAX_PAYLOAD_BYTES = env.int("EVENT_MAX_PAYLOAD_BYTES", default=100_000)
# Throttles on the ingestion endpoint: a per-IP cap (deterrent against
# credential stuffing with stolen keys) and a per-key cap (protects the
# endpoint from one misbehaving integration key starving everyone else).
EVENT_THROTTLE_IP = env("EVENT_THROTTLE_IP", default="5000/min")
EVENT_THROTTLE_KEY = env("EVENT_THROTTLE_KEY", default="1000/min")

# ---- Brute-force lockout (django-axes) ----
# Tracks failed logins per (IP, username) pair. The auth login view checks
# is_user_locked_out() and sends family user_login_failed itself — our login
# flow does not run through django.contrib.auth.authenticate().
AXES_ENABLED = env.bool("DJANGO_AXES_ENABLED", default=True)
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = env.float("AXES_COOLOFF_TIME_HOURS", default=0.25)  # 15 min
AXES_LOCKOUT_PARAMETERS = [["ip_address", "username"]]  # failures count per (IP, account) pair
# Our login credentials key the username by "email"; without this axes falls
# back to its "username" default and records every failure as anonymous,
# making lockout effectively per-IP instead of per-account.
AXES_USERNAME_FORM_FIELD = "email"
AXES_RESET_ON_SUCCESS = False  # successes clear attempts explicitly in the view
AXES_NEVER_LOCKOUT_WHITELIST = []

# Axes middleware/backend are intentionally not wired in: lockout is enforced
# inside LoginView so every response keeps the unified envelope (axes' own
# middleware short-circuits with a non-envelope body).
SILENCED_SYSTEM_CHECKS = ["axes.W002", "axes.W003"]

# ---- Google sign-in ----
# Empty in dev: /auth/google/ runs in stub mode (no token verification).
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")

# ---- Team invites (Phase 4A) ----
# How long an invite link stays valid before the token expires. Only the
# token's hash is stored (Agent.md rule 4); the raw token is shown once.
INVITE_TTL_MINUTES = env.int("INVITE_TTL_MINUTES", default=60 * 24 * 7)

# ---- OpenRouter / AI analysis (Phase 2B) ----
# Empty API key means analysis fails gracefully with status=failed (no retry
# storm) until a real key is configured. Base URL is the OpenRouter API root.
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_BASE_URL = env(
    "OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1"
)
# Ordered fallback chain tried on 429/error. Verified against OpenRouter's live
# model list Aug 2026 — the original spec's `qwen/qwen3-30b-a3b:free` was
# delisted; the free lineup rotates weekly, so re-check before shipping.
OPENROUTER_MODELS = env.list(
    "OPENROUTER_MODELS",
    default=[
        "openai/gpt-oss-20b:free",
        "deepseek/deepseek-r1:free",
        "openrouter/free",
    ],
)
OPENROUTER_TIMEOUT_SECONDS = env.int("OPENROUTER_TIMEOUT_SECONDS", default=90)

# Analysis caching window: repeats of an already-analyzed incident are not
# re-analyzed while the latest analysis is younger than this (spec §7 — this
# is what keeps us inside OpenRouter's free-tier rate limits).
AI_ANALYSIS_CACHE_HOURS = env.int("AI_ANALYSIS_CACHE_HOURS", default=6)
# The prompt is built from redacted, truncated error content so a giant
# stacktrace can't blow the model's context window.
AI_PROMPT_MAX_CHARS = env.int("AI_PROMPT_MAX_CHARS", default=20_000)
# 429 backoff: countdown = base * 2 ** attempt, capped by max retries.
AI_RETRY_BASE_SECONDS = env.int("AI_RETRY_BASE_SECONDS", default=60)
AI_RETRY_MAX_ATTEMPTS = env.int("AI_RETRY_MAX_ATTEMPTS", default=5)

# ---- Pusher realtime (Phase 3A) ----
# Empty app id/key/secret → publishing is a no-op (dev-safe). The secret is
# never exposed to any frontend code or API response — only POST /pusher/auth/
# signs private channels server-side.
PUSHER_APP_ID = env("PUSHER_APP_ID", default="")
PUSHER_KEY = env("PUSHER_KEY", default="")
PUSHER_SECRET = env("PUSHER_SECRET", default="")
PUSHER_CLUSTER = env("PUSHER_CLUSTER", default="mt1")
PUSHER_USE_TLS = env.bool("PUSHER_USE_TLS", default=True)
# Publishing is best-effort: a slow/unreachable Pusher must never slow the
# ingestion hot path, so client calls time out quickly and fail open.
PUSHER_PUBLISH_TIMEOUT_SECONDS = env.float(
    "PUSHER_PUBLISH_TIMEOUT_SECONDS", default=2.0
)

# ---- Slack integration (Phase 4D) ----
# Empty client id/secret → the connect endpoint fails with
# SLACK_NOT_CONFIGURED (same dev-safe convention as Pusher). Access tokens
# received from Slack are stored encrypted at rest (django-cryptography).
SLACK_CLIENT_ID = env("SLACK_CLIENT_ID", default="")
SLACK_CLIENT_SECRET = env("SLACK_CLIENT_SECRET", default="")
SLACK_OAUTH_URL = env("SLACK_OAUTH_URL", default="https://slack.com/api/oauth.v2.access")
SLACK_CHAT_POST_URL = env("SLACK_CHAT_POST_URL", default="https://slack.com/api/chat.postMessage")
SLACK_API_TIMEOUT_SECONDS = env.float("SLACK_API_TIMEOUT_SECONDS", default=5.0)

# ---- Alert dispatch (Phase 4D) ----
# Base URL used to build incident links inside alert messages.
APP_BASE_URL = env("APP_BASE_URL", default="http://localhost:3000")
# Outbound dispatch (webhook POSTs, Slack calls) must never hang the worker.
DISPATCH_TIMEOUT_SECONDS = env.float("DISPATCH_TIMEOUT_SECONDS", default=5.0)

# ---- Django REST Framework ----
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.AccessCookieJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Every DRF-raised error becomes {success, message, error: {code, fields?}}.
    "EXCEPTION_HANDLER": "trazeiq_backend.responses.drf_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# ---- API documentation (Swagger / OpenAPI) ----
# Exposed only where the environment opts in (dev by default, prod off).
# See dev.py / prod.py.
SPECTACULAR_SETTINGS = {
    "TITLE": "TrazeIQ API",
    "DESCRIPTION": (
        "Detect. Understand. Fix. — the API powering TrazeIQ's incident "
        "monitoring platform. Auth is cookie-based: sign in via the auth "
        "endpoints, and the browser sends the httpOnly refresh cookie "
        "(path `/api/v1/auth/`) plus the access cookie on every request. "
        "Alternative flows use an `Authorization: Bearer <token>` header."
    ),
    "VERSION": "v1",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "TAGS": [
        {"name": "auth", "description": "Registration, verification, login and password recovery"},
        {"name": "organizations", "description": "Tenant organizations and membership"},
        {"name": "projects", "description": "Projects and their API keys"},
        {"name": "events", "description": "Direct HTTP event ingestion and event querying"},
        {"name": "realtime", "description": "Pusher channel auth and live event publishing"},
    ],
}

# ---- SimpleJWT ----
from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

# ---- CORS (baseline — locked per-env, hardened again in Phase 5) ----
CORS_ALLOWED_ORIGINS = env.list(
    "DJANGO_CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"]
)
CORS_ALLOW_CREDENTIALS = True  # refresh-token cookie flows
CORS_ALLOW_ALL_ORIGINS = False

# ---- Cache ----
# DRF throttling (and anything else cache-backed) uses this. prod.py swaps in
# a Redis backend when DJANGO_REDIS_URL is configured.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "trazeiq-default",
    }
}

# ---- Celery (async jobs) ----
# Redis-backed broker/result backend. The worker process is started with
# `celery -A trazeiq_backend worker`; docker-compose runs Redis + Django + the
# worker together for local development. Nothing in the ingestion hot path may
# call a Celery task synchronously (Agent.md rule 1) — .delay() only.
from kombu import Queue  # noqa: E402

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env(
    "CELERY_RESULT_BACKEND", default="redis://localhost:6379/1"
)
# Celery 5.3+ stops retrying broker connections at startup by default; keep the
# worker waiting for Redis instead of dying on a brief startup race.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES = (
    Queue("default"),
    # Dedicated queue for OpenRouter analysis jobs. Kept separate from the
    # default queue so a burst of analysis jobs can't starve lighter tasks,
    # and rate-limited to stay under OpenRouter's ~20 requests/minute free
    # ceiling (see Project_Overview.md §7).
    Queue("ai_analysis"),
)
# Every task under apps/ai lands on the rate-limited AI queue. The glob covers
# Phase 2B's `analyze_incident` (and any future ai-app tasks) without needing a
# new route entry per task.
CELERY_TASK_ROUTES = {
    "apps.ai.tasks.*": {"queue": "ai_analysis", "rate_limit": "15/m"},
}
