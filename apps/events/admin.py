from django.contrib import admin

from .models import ErrorGroup, Event


@admin.register(ErrorGroup)
class ErrorGroupAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "count", "first_seen", "last_seen")
    list_filter = ("project",)
    search_fields = ("title", "fingerprint")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("level", "message", "project", "error_group", "environment", "service", "created_at")
    list_filter = ("project", "level", "environment", "service", "request_method")
    search_fields = ("message", "stacktrace", "endpoint", "user_id", "ip_address", "fingerprint")
    readonly_fields = ("created_at",)