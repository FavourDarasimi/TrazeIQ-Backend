from django.conf import settings

from rest_framework_simplejwt.authentication import JWTAuthentication


class AccessCookieJWTAuthentication(JWTAuthentication):
    """JWT authentication that reads the token from the frontend request.

    Tries the standard ``Authorization: Bearer <token>`` header first (an
    invalid header is still rejected, not silently ignored). When the header
    is absent it falls back to the httpOnly ``trazeiq_access`` cookie set at
    login — so browsers using cookie-only mode are authenticated too.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            return result

        raw = request.COOKIES.get(settings.AUTH_ACCESS_COOKIE_NAME)
        if raw:
            validated = self.get_validated_token(raw)
            return self.get_user(validated), validated
        return None