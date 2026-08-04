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
    "apps.notifications",
    "apps.analytics",
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

# ---- Per-endpoint rate limits (auth) ----
# Each auth endpoint is throttled per client IP. Rates are Django throttle
# strings ("N/min", "N/hour", ...) and are read by AuthScopedRateThrottle
# from these settings so environments can tune them via .env.
AUTH_THROTTLE_LOGIN = env("AUTH_THROTTLE_LOGIN", default="60/min")
AUTH_THROTTLE_REGISTER = env("AUTH_THROTTLE_REGISTER", default="60/min")
AUTH_THROTTLE_VERIFY = env("AUTH_THROTTLE_VERIFY", default="30/min")
AUTH_THROTTLE_RESEND_OTP = env("AUTH_THROTTLE_RESEND_OTP", default="10/min")
AUTH_THROTTLE_FORGOT = env("AUTH_THROTTLE_FORGOT", default="10/min")
AUTH_THROTTLE_RESET = env("AUTH_THROTTLE_RESET", default="20/min")
AUTH_THROTTLE_GOOGLE = env("AUTH_THROTTLE_GOOGLE", default="20/min")
AUTH_THROTTLE_REFRESH = env("AUTH_THROTTLE_REFRESH", default="30/min")

# ---- Brute-force lockout (django-axes) ----
# Tracks failed logins per (IP, username) pair. The auth login view checks
# is_user_locked_out() and sends family user_login_failed itself — our login
# flow does not run through django.contrib.auth.authenticate().
AXES_ENABLED = env.bool("DJANGO_AXES_ENABLED", default=True)
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = env.float("AXES_COOLOFF_TIME_HOURS", default=0.25)  # 15 min
AXES_LOCKOUT_PARAMETERS = [["ip_address", "username"]]  # failures count per (IP, account) pair
AXES_RESET_ON_SUCCESS = False  # successes clear attempts explicitly in the view
AXES_NEVER_LOCKOUT_WHITELIST = []

# Axes middleware/backend are intentionally not wired in: lockout is enforced
# inside LoginView so every response keeps the unified envelope (axes' own
# middleware short-circuits with a non-envelope body).
SILENCED_SYSTEM_CHECKS = ["axes.W002", "axes.W003"]

# ---- Google sign-in ----
# Empty in dev: /auth/google/ runs in stub mode (no token verification).
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")

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
