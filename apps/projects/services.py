from .models import Project
from .utils import api_key_prefix, generate_api_key, hash_api_key


def create_project(
    organization, name: str, environment: str
) -> tuple[Project, str]:
    """Create a project, returning it together with the raw API key.

    The raw key is generated once and only its HMAC digest is persisted.
    """
    raw_key = generate_api_key()
    project = Project.objects.create(
        organization=organization,
        name=name,
        environment=environment,
        api_key_hash=hash_api_key(raw_key),
        api_key_prefix=api_key_prefix(raw_key),
    )
    return project, raw_key


def update_project(project: Project, *, name=None, environment=None) -> Project:
    if name is not None:
        project.name = name
    if environment is not None:
        project.environment = environment
    project.save(update_fields=["name", "environment"])
    return project


def delete_project(project: Project) -> None:
    project.delete()


def integration_snippet(raw_key: str, environment: str) -> str:
    """Copy-paste direct HTTP snippet shown next to the raw key, once.

    The integration surface is a plain HTTPS POST — no packaged SDK is
    shipped (see Backend-Phases.md); this snippet is the whole integration.
    """
    return (
        "curl -X POST https://api.trazeiq.io/api/v1/events/ \\\n"
        '  -H "Content-Type: application/json" \\\n'
        f'  -H "X-API-Key: {raw_key}" \\\n'
        '  -d \'{"environment": "%s", "message": "Hello TrazeIQ"}\'' % environment
    )