from django.contrib import admin

from .models import AIAnalysis


@admin.register(AIAnalysis)
class AIAnalysisAdmin(admin.ModelAdmin):
    list_display = ("id", "incident", "status", "confidence", "model_used", "created_at")
    list_filter = ("status", "confidence", "model_used")
    search_fields = ("root_cause", "suggested_fix")
    readonly_fields = ("created_at",)
