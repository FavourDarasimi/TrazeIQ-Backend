"""OpenRouter HTTP client — the one place that talks to the LLM API.

Wrapped behind a thin function so the rest of the app (and the test suite)
never touches urllib directly. Errors are raised as our own exceptions so the
Celery task can decide between "retry with backoff" and "mark failed" without
knowing anything about HTTP.

Stdlib urllib only — matches the Google tokeninfo call in
``apps/accounts/services.py`` (no HTTP client dependency in this project).
"""

import json
import urllib.error
import urllib.request

from django.conf import settings

BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"


class OpenRouterError(Exception):
    """Base class for every OpenRouter client failure."""


class RateLimitError(OpenRouterError):
    """HTTP 429 — OpenRouter's free tier is throttling us. Retry with backoff."""


class OpenRouterAPIError(OpenRouterError):
    """Any other failure: missing key, 4xx/5xx, network error, timeout."""


def call_openrouter(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> str:
    """One chat completion call. Returns the model's raw text response.

    Raises :class:`RateLimitError` on 429 and :class:`OpenRouterAPIError` on
    every other failure — callers never see urllib exceptions.
    """
    api_key = api_key if api_key is not None else settings.OPENROUTER_API_KEY
    if not api_key:
        raise OpenRouterAPIError("OPENROUTER_API_KEY is not configured")
    base_url = (base_url if base_url is not None else settings.OPENROUTER_BASE_URL).rstrip("/")
    timeout = timeout if timeout is not None else settings.OPENROUTER_TIMEOUT_SECONDS

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "TrazeIQ",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code == 429:
            raise RateLimitError(f"OpenRouter 429: {detail}") from exc
        raise OpenRouterAPIError(
            f"OpenRouter {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OpenRouterAPIError(f"OpenRouter request failed: {exc}") from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterAPIError(
            "OpenRouter response missing choices[0].message.content"
        ) from exc
