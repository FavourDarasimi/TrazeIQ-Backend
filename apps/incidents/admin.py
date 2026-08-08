from django.contrib import admin

from .models import Incident, TimelineEntry


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "error_group", "project", "severity", "status", "assigned_to", "created_at", "resolved_at")
    list_filter = ("severity", "status", "project")
    search_fields = ("error_group__title",)
    readonly_fields = ("created_at",)


@admin.register(TimelineEntry)
class TimelineEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "incident", "kind", "actor", "created_at")
    list_filter = ("kind",)
    search_fields = ("content", "incident__error_group__title")
    readonly_fields = ("created_at",)