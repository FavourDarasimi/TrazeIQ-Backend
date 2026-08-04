from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "api_key_prefix", "environment", "created_at")
    list_filter = ("organization", "environment")
    search_fields = ("name", "api_key_prefix")
