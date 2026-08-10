from django.contrib import admin

from .models import AlertLog, AlertRule


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "channel", "cooldown_minutes", "created_at")
    list_filter = ("channel",)


@admin.register(AlertLog)
class AlertLogAdmin(admin.ModelAdmin):
    list_display = ("rule", "incident", "dispatched_at")
    list_select_related = ("rule", "incident")