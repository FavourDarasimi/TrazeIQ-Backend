"""Phase 5C: invalidate dashboard caches on tenant writes.

An ``Event`` or ``Incident`` write changes a project's aggregates, so we bump
that project's dashboard version. The cache key (see ``apps.analytics.cache``)
encodes the version, so bumping it orphans the stale entry and the next read
recomputes — no per-user fan-out needed. Connected from ``AnalyticsConfig.
ready()`` so it loads once, after every app is initialized.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.events.models import Event
from apps.incidents.models import Incident


@receiver(post_save, sender=Event)
def _bump_on_event(sender, instance, **kwargs):
    from apps.analytics.cache import bump_project_dashboard_version

    bump_project_dashboard_version(instance.project_id)


@receiver(post_save, sender=Incident)
def _bump_on_incident(sender, instance, **kwargs):
    from apps.analytics.cache import bump_project_dashboard_version

    bump_project_dashboard_version(instance.project_id)
