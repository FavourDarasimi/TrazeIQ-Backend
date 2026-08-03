from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .cookies import (
    clear_access_cookie,
    clear_refresh_cookie,
    get_refresh_cookie,
    set_access_cookie,
    set_refresh_cookie,
)
from .serializers import (
    AuthSessionSerializer,
    DetailResponseSerializer,
    ForgotPasswordSerializer,
    GoogleAuthSerializer,
    LoginSerializer,
    RegisterSerializer,
    ResendOTPSerializer,
    ResetPasswordSerializer,
    UserOutSerializer,
    VerifyEmailSerializer,
)
from .services import (
    authenticate_user,
    forgot_password,
    google_authenticate,
    logout_user,
    register_user,
    resend_otp,
    reset_password,
    rotate_refresh_token,
    tokens_for,
    verify_email,
)


class _AuthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


def _signed_in_response(user) -> Response:
    """Body carries the user; tokens ride in httpOnly cookies only."""
    tokens = tokens_for(user)
    response = Response({"user": UserOutSerializer(user).data})
    set_access_cookie(response, tokens["access"])
    set_refresh_cookie(response, tokens["refresh"])
    return response


def _otp_error(reason: str) -> str:
    return {
        "used": "This code has already been used.",
        "expired": "This code has expired. Request a new one.",
        "too_many_attempts": "Too many incorrect attempts. Request a new code.",
        "invalid": "Incorrect code.",
    }.get(reason, "Invalid code.")


class RegisterView(_AuthView):
    @extend_schema(
        tags=["auth"],
        summary="Register a user",
        description=(
            "Creates an inactive account and emails a 6-digit verification "
            "code. The account can only log in after POST /auth/verify/ "
            "confirms the code."
        ),
        request=RegisterSerializer,
        responses={
            201: inline_serializer(
                "RegisterOk",
                fields={
                    "detail": serializers.CharField(),
                    "email": serializers.EmailField(),
                },
            ),
            400: DetailResponseSerializer,
            409: inline_serializer(
                "RegisterConflict",
                fields={"detail": serializers.CharField()},
            ),
        },
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = register_user(
            email=data["email"],
            password=data["password"],
            name=data.get("name", ""),
        )
        if user is None:
            return Response(
                {"detail": "An account with this email already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "detail": "Registration successful. A 6-digit verification code "
                "was sent to your email.",
                "email": data["email"],
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(_AuthView):
    @extend_schema(
        tags=["auth"],
        summary="Verify email with the emailed code",
        description=(
            "Consumes the 6-digit code emailed at registration. On success the "
            "user is active and this call logs them in (tokens set as cookies)."
        ),
        request=VerifyEmailSerializer,
        responses={
            200: AuthSessionSerializer,
            400: DetailResponseSerializer,
        },
    )
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ok, reason, user = verify_email(email=data["email"], code=data["otp"])
        if not ok:
            return Response(
                {"detail": _otp_error(reason)}, status=status.HTTP_400_BAD_REQUEST
            )
        return _signed_in_response(user)


class ResendOTPView(_AuthView):
    @extend_schema(
        tags=["auth"],
        summary="Resend a verification or reset code",
        description=(
            "Re-issues an OTP for the given purpose (email_verification | "
            "password_reset). Returns 202 even when the address has no account, "
            "so this endpoint cannot be used to probe registered emails."
        ),
        request=ResendOTPSerializer,
        responses={
            202: DetailResponseSerializer,
            400: DetailResponseSerializer,
        },
    )
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = resend_otp(email=data["email"], purpose=data["purpose"])
        if result == "already_verified":
            return Response(
                {"detail": "This email address is already verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"detail": "If that account exists, a code was sent."},
            status=status.HTTP_202_ACCEPTED,
        )


class LoginView(_AuthView):
    @extend_schema(
        tags=["auth"],
        summary="Log in with email + password",
        description=(
            "Returns the user and sets two httpOnly cookies: trazeiq_refresh "
            "(7 days, path /api/v1/auth/) and trazeiq_access (15 min, path /). "
            "Access tokens are never returned in the body."
        ),
        request=LoginSerializer,
        responses={
            200: AuthSessionSerializer,
            401: DetailResponseSerializer,
            403: inline_serializer(
                "EmailNotVerified",
                fields={"detail": serializers.CharField()},
            ),
        },
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = authenticate_user(email=data["email"], password=data["password"])
        if user is None:
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not user.email_verified or not user.is_active:
            return Response(
                {"detail": "email_not_verified"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return _signed_in_response(user)


class RefreshView(_AuthView):
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
            200: AuthSessionSerializer,
            401: DetailResponseSerializer,
        },
    )
    def post(self, request):
        result = rotate_refresh_token(get_refresh_cookie(request) or "")
        if result is None:
            return Response(
                {"detail": "refresh_token_invalid"}, status=status.HTTP_401_UNAUTHORIZED
            )
        user, tokens = result

        response = Response({"user": UserOutSerializer(user).data})
        set_access_cookie(response, tokens["access"])
        set_refresh_cookie(response, tokens["refresh"])
        return response


class LogoutView(_AuthView):
    @extend_schema(
        tags=["auth"],
        summary="Log out",
        description=(
            "Blacklists the current refresh token and clears both auth cookies."
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
            "trazeiq_access httpOnly cookie, and returns the signed-in user."
        ),
        responses={
            200: UserOutSerializer,
            401: DetailResponseSerializer,
        },
    )
    def get(self, request):
        return Response(UserOutSerializer(request.user).data)


class GoogleAuthView(_AuthView):
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
            200: AuthSessionSerializer,
            400: DetailResponseSerializer,
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
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return _signed_in_response(user)


class ForgotPasswordView(_AuthView):
    @extend_schema(
        tags=["auth"],
        summary="Request a password reset code",
        description=(
            "Emails a password_reset OTP when the address has an account. "
            "Always returns 200 so the endpoint cannot be used to probe "
            "registered addresses."
        ),
        request=ForgotPasswordSerializer,
        responses={200: DetailResponseSerializer},
    )
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        forgot_password(email=serializer.validated_data["email"])
        # Do not reveal whether the address has an account.
        return Response(
            {"detail": "If this account exists, a reset code was sent."},
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(_AuthView):
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
            200: DetailResponseSerializer,
            400: DetailResponseSerializer,
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
            return Response(
                {"detail": _otp_error(reason)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"detail": "Password reset complete."})