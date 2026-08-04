from django.conf import settings
from django.contrib.auth.signals import user_login_failed
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from axes.handlers.proxy import AxesProxyHandler

from trazeiq_backend.responses import (
    ErrorCode,
    api_error,
    api_success,
    envelope_schema,
)

from .throttles import AuthScopedRateThrottle

from .cookies import (
    clear_access_cookie,
    clear_refresh_cookie,
    get_refresh_cookie,
    set_access_cookie,
    set_refresh_cookie,
)
from .serializers import (
    CompleteRegistrationSerializer,
    ForgotPasswordSerializer,
    GoogleAuthSerializer,
    LoginSerializer,
    RequestOTPSerializer,
    ResetPasswordSerializer,
    UserOutSerializer,
    VerifyOTPSerializer,
)
from .services import (
    authenticate_user,
    complete_registration,
    forgot_password,
    google_authenticate,
    logout_user,
    registration_email_cap_exhausted,
    request_registration_otp,
    reset_password,
    rotate_refresh_token,
    tokens_for,
    verify_registration_otp,
)
from .selectors import user_exists


class _AuthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthScopedRateThrottle]


def _is_locked_out(request, email: str) -> bool:
    """True when axes has locked this (IP, username) pair out of login."""
    if not settings.AXES_ENABLED:
        return False
    # AXES_USERNAME_FORM_FIELD resolves to User.USERNAME_FIELD — "email" here.
    return AxesProxyHandler.is_locked(request, credentials={"email": email})


def _lockout_response() -> Response:
    """Envelope-consistent 429 for a locked account."""
    cooloff = getattr(settings, "AXES_COOLOFF_TIME", 0.25) * 3600
    response = api_error(
        ErrorCode.TOO_MANY_REQUESTS,
        "Too many failed login attempts. Try again later.",
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response["Retry-After"] = str(int(cooloff))
    return response


def _signed_in_response(user) -> Response:
    """Body carries ``data.user``; tokens ride in httpOnly cookies only."""
    tokens = tokens_for(user)
    response = api_success(data={"user": UserOutSerializer(user).data})
    set_access_cookie(response, tokens["access"])
    set_refresh_cookie(response, tokens["refresh"])
    return response


def _otp_message(reason: str) -> str:
    return {
        "used": "This code has already been used.",
        "expired": "This code has expired. Request a new one.",
        "too_many_attempts": "Too many incorrect attempts. Request a new code.",
        "invalid": "Incorrect code.",
    }.get(reason, "Invalid code.")


class RegisterRequestOTPView(_AuthView):
    throttle_scope = "auth_register_request"

    @extend_schema(
        tags=["auth"],
        operation_id="register_request",
        summary="Signup step 1 — request a verification code",
        description=(
            "Emails a 6-digit code to an address that has no account yet. "
            "No user is created here; the address only becomes an account after "
            "POST /auth/register/complete/. Responds 409 when the address "
            "already belongs to an account (the client switches to login) and "
            "429 when this address has asked for codes too often."
        ),
        request=RequestOTPSerializer,
        responses={
            200: envelope_schema("RegisterRequestOk"),
            400: envelope_schema("RegisterRequestValidation", error=True),
            409: envelope_schema("RegisterRequestConflict", error=True),
            429: envelope_schema("RegisterRequestThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = RequestOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        if user_exists(email):
            return api_error(
                ErrorCode.EMAIL_TAKEN,
                "An account with this email already exists.",
                status=status.HTTP_409_CONFLICT,
            )
        if registration_email_cap_exhausted(email):
            return api_error(
                ErrorCode.TOO_MANY_REQUESTS,
                "Too many codes requested for this email. Try again later.",
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        request_registration_otp(email)
        return api_success(
            message="A 6-digit verification code was sent to your email."
        )


class RegisterVerifyOTPView(_AuthView):
    throttle_scope = "auth_register_verify"

    @extend_schema(
        tags=["auth"],
        operation_id="register_verify",
        summary="Signup step 2 — prove the email with the code",
        description=(
            "Consumes the code emailed at step 1 and returns a single-use "
            "registration token (15 min TTL). The token is shown exactly once "
            "and must be sent to POST /auth/register/complete/ together with "
            "the new password."
        ),
        request=VerifyOTPSerializer,
        responses={
            200: envelope_schema(
                "RegisterVerifyOk",
                payload=inline_serializer(
                    "RegisterVerifyData",
                    fields={
                        "registration_token": serializers.CharField(),
                    },
                ),
            ),
            400: envelope_schema("RegisterVerifyError", error=True),
            429: envelope_schema("RegisterVerifyThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ok, reason, raw_token = verify_registration_otp(
            email=data["email"], code=data["otp"]
        )
        if not ok:
            return api_error(
                ErrorCode.otp(reason),
                _otp_message(reason),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return api_success(
            data={"registration_token": raw_token},
            message="Email verified. Choose your password to finish signing up.",
        )


class RegisterCompleteView(_AuthView):
    throttle_scope = "auth_register_complete"

    @extend_schema(
        tags=["auth"],
        operation_id="register_complete",
        summary="Signup step 3 — create the account and log in",
        description=(
            "Creates the user (verified + active) from the registration token, "
            "password and password confirmation, then signs the account in with "
            "the same cookie session as login. The token is single-use: "
            "replaying it is rejected."
        ),
        request=CompleteRegistrationSerializer,
        responses={
            200: envelope_schema(
                "RegisterCompleteOk",
                payload=inline_serializer(
                    "RegisterCompleteData",
                    fields={"user": UserOutSerializer()},
                ),
            ),
            400: envelope_schema("RegisterCompleteError", error=True),
            409: envelope_schema("RegisterCompleteConflict", error=True),
            429: envelope_schema("RegisterCompleteThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = CompleteRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ok, reason, user = complete_registration(
            registration_token=data["registration_token"],
            password=data["password"],
        )
        if not ok:
            if reason == "email_taken":
                return api_error(
                    ErrorCode.EMAIL_TAKEN,
                    "An account with this email already exists.",
                    status=status.HTTP_409_CONFLICT,
                )
            if reason == "expired_token":
                return api_error(
                    ErrorCode.REGISTRATION_TOKEN_EXPIRED,
                    "This signup session has expired. Start again with your "
                    "email address.",
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return api_error(
                ErrorCode.REGISTRATION_TOKEN_INVALID,
                "This signup session is invalid or has already been used. "
                "Start again with your email address.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _signed_in_response(user)


class LoginView(_AuthView):
    throttle_scope = "auth_login"

    @extend_schema(
        tags=["auth"],
        summary="Log in with email + password",
        description=(
            "Sends the user in ``data.user`` and sets two httpOnly cookies: "
            "trazeiq_refresh (7 days, path /api/v1/auth/) and trazeiq_access "
            "(15 min, path /). Access tokens are never returned in the body."
        ),
        request=LoginSerializer,
        responses={
            200: envelope_schema(
                "LoginOk",
                payload=inline_serializer(
                    "LoginData",
                    fields={"user": UserOutSerializer()},
                ),
            ),
            401: envelope_schema("LoginUnauthorized", error=True),
            403: envelope_schema("LoginUnverified", error=True),
            429: envelope_schema("LoginThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"]
        password = data["password"]

        # Brute-force lockout: skip the password check entirely for a locked pair.
        if _is_locked_out(request, email):
            return _lockout_response()

        user = authenticate_user(email=email, password=password)
        if user is None:
            # Count the failure against this (IP, username) pair via the django-axes
            # handler (connected to user_login_failed). Our login flow bypasses
            # django.contrib.auth.authenticate(), so the signal is sent manually.
            user_login_failed.send(
                sender=LoginView,
                request=request,
                credentials={"email": email},
            )
            return api_error(
                ErrorCode.INVALID_CREDENTIALS,
                "Invalid email or password.",
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not user.email_verified or not user.is_active:
            return api_error(
                ErrorCode.EMAIL_NOT_VERIFIED,
                "Verify your email address before logging in.",
                status=status.HTTP_403_FORBIDDEN,
            )

        if settings.AXES_ENABLED:
            AxesProxyHandler.reset_attempts(username=email)
        return _signed_in_response(user)


class RefreshView(_AuthView):
    throttle_scope = "auth_refresh"

    @extend_schema(
        tags=["auth"],
        summary="Rotate the access token using the refresh cookie",
        description=(
            "Reads the refresh JWT from the trazeiq_refresh httpOnly cookie, "
            "blacklists it (single-use rotation) and issues a fresh access + "
            "refresh pair back as cookies."
        ),
        request=None,
        responses={
            200: envelope_schema(
                "RefreshOk",
                payload=inline_serializer(
                    "RefreshData",
                    fields={"user": UserOutSerializer()},
                ),
            ),
            401: envelope_schema("RefreshError", error=True),
            429: envelope_schema("RefreshThrottled", error=True),
        },
    )
    def post(self, request):
        result = rotate_refresh_token(get_refresh_cookie(request) or "")
        if result is None:
            return api_error(
                ErrorCode.REFRESH_TOKEN_INVALID,
                "The refresh token is invalid or has already been used.",
                status=status.HTTP_401_UNAUTHORIZED,
            )
        user, tokens = result

        response = api_success(data={"user": UserOutSerializer(user).data})
        set_access_cookie(response, tokens["access"])
        set_refresh_cookie(response, tokens["refresh"])
        return response


class LogoutView(_AuthView):
    @extend_schema(
        tags=["auth"],
        summary="Log out",
        description=(
            "Blacklists the current refresh token and clears both auth cookies. "
            "Responds 204 with no body."
        ),
        request=None,
        responses={204: None},
    )
    def post(self, request):
        logout_user(get_refresh_cookie(request))
        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_access_cookie(response)
        clear_refresh_cookie(response)
        return response


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["auth"],
        summary="Current user",
        description=(
            "Authenticates via the Authorization Bearer header or the "
            "trazeiq_access httpOnly cookie, and returns the signed-in user "
            "under ``data.user``."
        ),
        responses={
            200: envelope_schema(
                "MeOk",
                payload=inline_serializer(
                    "MeData",
                    fields={"user": UserOutSerializer()},
                ),
            ),
            401: envelope_schema("MeError", error=True),
        },
    )
    def get(self, request):
        return api_success({"user": UserOutSerializer(request.user).data})


class GoogleAuthView(_AuthView):
    throttle_scope = "auth_google"

    @extend_schema(
        tags=["auth"],
        summary="Sign up / sign in with Google",
        description=(
            "Stub mode (no GOOGLE_CLIENT_ID configured): the request email is "
            "trusted directly. Real mode: id_token is verified against Google's "
            "tokeninfo endpoint and the token payload wins. Returns the same "
            "cookie session as login."
        ),
        request=GoogleAuthSerializer,
        responses={
            200: envelope_schema(
                "GoogleOk",
                payload=inline_serializer(
                    "GoogleData",
                    fields={"user": UserOutSerializer()},
                ),
            ),
            400: envelope_schema("GoogleError", error=True),
            429: envelope_schema("GoogleThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            user = google_authenticate(
                email=data["email"],
                id_token=data.get("id_token", ""),
                name=data.get("name", ""),
            )
        except ValueError as exc:
            return api_error(
                ErrorCode.GOOGLE_AUTH_FAILED,
                str(exc),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _signed_in_response(user)


class ForgotPasswordView(_AuthView):
    throttle_scope = "auth_forgot"

    @extend_schema(
        tags=["auth"],
        summary="Request a password reset code",
        description=(
            "Emails a password_reset OTP when the address has an account. "
            "Always returns 200 so the endpoint cannot be used to probe "
            "registered addresses."
        ),
        request=ForgotPasswordSerializer,
        responses={
            200: envelope_schema("ForgotOk"),
            429: envelope_schema("ForgotThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        forgot_password(email=serializer.validated_data["email"])
        # Do not reveal whether the address has an account.
        return api_success(message="If this account exists, a reset code was sent.")


class ResetPasswordView(_AuthView):
    throttle_scope = "auth_reset"

    @extend_schema(
        tags=["auth"],
        summary="Reset the password",
        description=(
            "Consumes the password_reset code (see /auth/forgot-password/) and "
            "replaces the password. The old password stops working immediately; "
            "all other outstanding reset codes for the user are voided."
        ),
        request=ResetPasswordSerializer,
        responses={
            200: envelope_schema("ResetOk"),
            400: envelope_schema("ResetError", error=True),
            429: envelope_schema("ResetThrottled", error=True),
        },
    )
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ok, reason = reset_password(
            email=data["email"],
            code=data["otp"],
            new_password=data["new_password"],
        )
        if not ok:
            return api_error(
                ErrorCode.otp(reason),
                _otp_message(reason),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return api_success(message="Password reset complete.")