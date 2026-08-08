from uuid import UUID

from .models import Project


def list_projects_for_user(user):
    """Projects in orgs the user is a member of, newest first."""
    return Project.objects.filter(
        organization__memberships__user=user
    ).distinct()


def get_project_for_user(project_id: UUID, user):
    """A single project the user can access, or ``None``.

    Scoped through membership like every tenant queryset — unknown ids and
    other orgs' projects both resolve to ``None`` and surface as 404.
    """
    return (
        Project.objects.filter(
            organization__memberships__user=user, id=project_id
        )
        .distinct()
        .first()
    )