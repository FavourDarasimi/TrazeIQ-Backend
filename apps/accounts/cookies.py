from django.conf import settings


def set_refresh_cookie(response, refresh_token: str) -> None:
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        refresh_token,
        max_age=settings.AUTH_COOKIE_MAX_AGE,
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path="/api/v1/auth/",
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(
        settings.AUTH_COOKIE_NAME,
        path="/api/v1/auth/",
    )


def get_refresh_cookie(request) -> str | None:
    return request.COOKIES.get(settings.AUTH_COOKIE_NAME)


def set_access_cookie(response, access_token: str) -> None:
    response.set_cookie(
        settings.AUTH_ACCESS_COOKIE_NAME,
        access_token,
        max_age=settings.AUTH_ACCESS_COOKIE_MAX_AGE,
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )


def clear_access_cookie(response) -> None:
    response.delete_cookie(
        settings.AUTH_ACCESS_COOKIE_NAME,
        path="/",
    )
