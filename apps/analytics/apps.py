from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = 'apps.analytics'

    def ready(self):
        # Phase 5C: connect dashboard-cache invalidation signals.
        from apps.analytics import signals  # noqa: F401
