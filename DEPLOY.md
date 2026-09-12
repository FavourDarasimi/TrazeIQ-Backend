# TrazeIQ backend — production deploy guide

Generic, host-agnostic, no Docker: plain Python + gunicorn with
`DJANGO_ENV=prod` on any host (VPS, Render, Railway, Fly, …); only the
Postgres/Redis provisioning differs per platform.

## What you need

- Python 3.12, PostgreSQL 14+, optional Redis 7+
- All secrets as environment variables — never commit `.env`
  (`server/.env` is gitignored; `.env.example` documents every variable)

## 1. Environment

Generate a secret key (do this once, store it in your host's secret manager):

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Required in prod — the server **fails fast** at boot if any are missing:

| Variable | Example |
|---|---|
| `DJANGO_ENV` | `prod` |
| `DJANGO_SECRET_KEY` | output of the command above |
| `DJANGO_ALLOWED_HOSTS` | `api.yourdomain.com` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | your database credentials |
| `POSTGRES_HOST` / `POSTGRES_PORT` | host, `5432` |
| `DJANGO_CORS_ALLOWED_ORIGINS` | `https://app.yourdomain.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://app.yourdomain.com,https://api.yourdomain.com` |

Production essentials (wrong values here are the usual day-one outage):

- `AUTH_DEV_OTP` — **leave blank**. Any value is accepted as a universal
  login-bypass OTP for every account.
- `GOOGLE_CLIENT_ID` — set it, or Google sign-in runs in trust-any-email
  stub mode.
- `API_KEY_HASH_SECRET` — set a distinct value (falls back to
  `DJANGO_SECRET_KEY`, coupling API-key hashes to secret rotation).
- `APP_BASE_URL` — the real `https://` frontend URL (incident links in
  emails/Slack/webhooks).
- Email SMTP (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
  `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`) — without
  these, invites and alert emails only print to server logs.
- `DJANGO_BEHIND_PROXY=True` when TLS terminates at a load balancer that
  sets `X-Forwarded-Proto: https`; keep `DJANGO_SECURE_SSL_REDIRECT=True`
  unless the proxy already forces HTTPS (otherwise redirect loops).
- `DJANGO_REDIS_URL` (e.g. `redis://host:6379/2`) — optional but
  recommended: without it, rate-limit counters are per-process and
  under-count across gunicorn workers.
- `DEBUG` defaults to `False` in prod; API docs default off
  (`DJANGO_ENABLE_API_SCHEMA`).

## 2. Release steps (every deploy)

```bash
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput   # required by WhiteNoise manifest storage
gunicorn trazeiq_backend.wsgi:application \
  --bind 0.0.0.0:8000 --workers 3 --timeout 60
```

On PaaS native-Python runtimes, split the same sequence: build command
`pip install -r requirements.txt && python manage.py collectstatic
--noinput`, release/start command `python manage.py migrate --noinput`
then gunicorn as above. Point a load-balancer health probe at
`GET /api/health/`.

(`docker-compose.yml` at the repo root is **dev only**: `DJANGO_ENV=dev`,
`DEBUG=True`, SQLite, `runserver`. Do not point it at production data.)

## 3. Verify

- `GET /api/health/` → `{"status": "ok"}` with timed `checks`
  (database/cache) and `metrics`. Always HTTP 200; `status: "degraded"`
  means a check is failing.
- `manage.py check` passes; full suite green:
  `DJANGO_ENV=test venv/bin/python manage.py test`
- From a non-allowed origin, API requests are blocked by CORS; security
  headers (HSTS, secure cookies) present — covered by
  `trazeiq_backend/tests/test_prod_security.py`.

## 4. First boot

- Create the operator account for the staff-only `/api/v1/admin/*`
  monitoring API: `python manage.py createsuperuser` (grants `is_staff`).
- Confirm `AUTH_DEV_OTP` is empty and `POST /api/v1/auth/google/` verifies
  real tokens (`GOOGLE_CLIENT_ID` set).

## 5. Know your limits (single-process design)

- No worker: alert dispatch runs **synchronously** in the request path.
  Outbound calls (Slack/webhook) are capped by `SLACK_API_TIMEOUT_SECONDS`
  / `DISPATCH_TIMEOUT_SECONDS` (default 5s), so gunicorn `--timeout 60`
  has ample headroom.
- `SECRET_KEY` rotation invalidates JWTs, Fernet-encrypted Slack tokens,
  and (if `API_KEY_HASH_SECRET` is unset) every project API key. Rotate
  `API_KEY_HASH_SECRET` independently instead where possible.
