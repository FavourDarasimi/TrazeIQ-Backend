from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "actor", "action", "target"]
    list_filter = ["action", "organization"]
    readonly_fields = ["created_at"]
