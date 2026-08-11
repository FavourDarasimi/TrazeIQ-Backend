from django.contrib import admin

from .models import SlackIntegration


@admin.register(SlackIntegration)
class SlackIntegrationAdmin(admin.ModelAdmin):
    list_display = ("organization", "team_name", "connected_at")