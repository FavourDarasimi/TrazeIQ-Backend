from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import OTPCode, RegistrationToken, User


@admin.register(User)
class UserAdminConfig(UserAdmin):
    ordering = ("email",)
    search_fields = ("email",)
    list_display = ("email", "auth_provider", "email_verified", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active", "auth_provider", "email_verified")
    readonly_fields = ("last_login", "date_joined")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "email_verified", "auth_provider", "google_sub")}),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "is_active", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2"),
        }),
    )


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "user", "purpose", "attempts", "used_at", "expires_at", "created_at")
    list_filter = ("purpose", "created_at")
    search_fields = ("email", "user__email")
    readonly_fields = ("code_hash",)


@admin.register(RegistrationToken)
class RegistrationTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "used_at", "expires_at", "created_at")
    search_fields = ("email",)
    readonly_fields = ("token_hash",)