from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'apps.accounts'

    def ready(self):
        # Register this app's drf-spectacular extensions (auth scheme docs).
        from apps.accounts.extensions import AccessCookieJWTAuthScheme  # noqa: F401
