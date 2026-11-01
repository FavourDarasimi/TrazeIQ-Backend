from uuid import UUID
from django.db.models import QuerySet

from .models import Deployment, Release


def get_project_deployments(
    project_id: UUID | str,
    environment: str | None = None,
    limit: int = 50,
) -> QuerySet[Deployment]:
    """Retrieve deployments for a project, optionally filtered by environment."""
    qs = Deployment.objects.filter(project_id=project_id).select_related("release", "project")
    if environment:
        qs = qs.filter(environment=environment)
    return qs[:limit]


def get_project_releases(
    project_id: UUID | str,
    limit: int = 50,
) -> QuerySet[Release]:
    """Retrieve unique releases defined for a project, newest first."""
    return Release.objects.filter(project_id=project_id)[:limit]


def get_latest_deployment_before(
    project_id: UUID | str,
    environment: str | None,
    timestamp,
) -> Deployment | None:
    """Find the latest completed deployment in the project environment prior to timestamp."""
    qs = Deployment.objects.filter(
        project_id=project_id,
        status__in=[Deployment.Status.COMPLETED, Deployment.Status.HEALTHY],
        deployed_at__lte=timestamp,
    ).select_related("release")
    if environment:
        qs = qs.filter(environment=environment)
    return qs.order_by("-deployed_at").first()
