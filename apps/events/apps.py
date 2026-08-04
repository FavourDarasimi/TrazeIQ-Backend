from django.apps import AppConfig


class EventsConfig(AppConfig):
    name = 'apps.events'

    def ready(self):
        # Register this app's drf-spectacular extensions (auth scheme docs).
        from apps.events.extensions import APIKeyAuthScheme  # noqa: F401
