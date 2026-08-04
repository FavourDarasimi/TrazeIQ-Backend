from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path(
        "register/request-otp/",
        views.RegisterRequestOTPView.as_view(),
        name="register-request-otp",
    ),
    path(
        "register/verify-otp/",
        views.RegisterVerifyOTPView.as_view(),
        name="register-verify-otp",
    ),
    path(
        "register/complete/",
        views.RegisterCompleteView.as_view(),
        name="register-complete",
    ),
    path("login/", views.LoginView.as_view(), name="login"),
    path("refresh/", views.RefreshView.as_view(), name="refresh"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("me/", views.MeView.as_view(), name="me"),
    path("google/", views.GoogleAuthView.as_view(), name="google"),
    path("forgot-password/", views.ForgotPasswordView.as_view(), name="forgot-password"),
    path("reset-password/", views.ResetPasswordView.as_view(), name="reset-password"),
]
