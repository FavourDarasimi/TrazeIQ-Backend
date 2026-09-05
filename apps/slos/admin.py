from django.contrib import admin

from .models import SLO, SLOBreachAlert, SLOBudgetSnapshot


@admin.register(SLO)
class SLOAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "target_pct", "window_days", "enabled")
    list_filter = ("enabled", "window_days")
    search_fields = ("name", "project__name")


@admin.register(SLOBudgetSnapshot)
class SLOBudgetSnapshotAdmin(admin.ModelAdmin):
    list_display = ("slo", "evaluated_at", "status", "budget_remaining_pct")
    list_filter = ("status",)


@admin.register(SLOBreachAlert)
class SLOBreachAlertAdmin(admin.ModelAdmin):
    list_display = ("slo", "kind", "fired_at", "acknowledged_at")
    list_filter = ("kind",)