from datetime import timedelta
from django.utils import timezone

from .models import ApiKeyScope, ApiKeyStatus, Project, ProjectApiKey
from .utils import api_key_prefix, generate_api_key, hash_api_key


def create_project(
    organization, name: str, environment: str, user=None
) -> tuple[Project, str]:
    """Create a project, returning it together with the raw API key.

    The raw key is generated once and only its HMAC digest is persisted.
    A corresponding ProjectApiKey record is automatically provisioned.
    """
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    prefix = api_key_prefix(raw_key)
    project = Project.objects.create(
        organization=organization,
        name=name,
        environment=environment,
        api_key_hash=key_hash,
        api_key_prefix=prefix,
    )
    ProjectApiKey.objects.create(
        project=project,
        name="Default Key",
        api_key_hash=key_hash,
        api_key_prefix=prefix,
        scopes=[ApiKeyScope.INGEST_ONLY],
        status=ApiKeyStatus.ACTIVE,
        created_by=user,
    )
    return project, raw_key


def update_project(project: Project, *, name=None, environment=None) -> Project:
    if name is not None:
        project.name = name
    if environment is not None:
        project.environment = environment
    project.save(update_fields=["name", "environment"])
    return project


def rotate_project_key(
    project: Project, *, grace_period_hours: int = 0, user=None
) -> tuple[Project, str]:
    """Regenerate the project's primary API key.

    If ``grace_period_hours`` > 0, the active key is moved to ``grace_period``
    status and remains valid until ``expires_at``. If 0, the previous key is
    revoked immediately.
    """
    raw_key = generate_api_key()
    new_hash = hash_api_key(raw_key)
    new_prefix = api_key_prefix(raw_key)
    now = timezone.now()

    # Find the current matching key if present
    old_key = project.api_keys.filter(
        api_key_hash=project.api_key_hash, status=ApiKeyStatus.ACTIVE
    ).first()
    if old_key is not None:
        if grace_period_hours > 0:
            old_key.status = ApiKeyStatus.GRACE_PERIOD
            old_key.expires_at = now + timedelta(hours=grace_period_hours)
            old_key.save(update_fields=["status", "expires_at"])
        else:
            old_key.status = ApiKeyStatus.REVOKED
            old_key.revoked_at = now
            old_key.save(update_fields=["status", "revoked_at"])

    project.api_key_hash = new_hash
    project.api_key_prefix = new_prefix
    project.save(update_fields=["api_key_hash", "api_key_prefix"])

    ProjectApiKey.objects.create(
        project=project,
        name="Rotated Key",
        api_key_hash=new_hash,
        api_key_prefix=new_prefix,
        scopes=[ApiKeyScope.INGEST_ONLY],
        status=ApiKeyStatus.ACTIVE,
        created_by=user,
    )

    return project, raw_key


def create_project_api_key(
    project: Project,
    name: str,
    scopes: list[str] | None = None,
    user=None,
) -> tuple[ProjectApiKey, str]:
    """Provision a new scoped API key for a project."""
    raw_key = generate_api_key()
    new_hash = hash_api_key(raw_key)
    new_prefix = api_key_prefix(raw_key)

    valid_scopes = scopes or [ApiKeyScope.INGEST_ONLY]

    key = ProjectApiKey.objects.create(
        project=project,
        name=name,
        api_key_hash=new_hash,
        api_key_prefix=new_prefix,
        scopes=valid_scopes,
        status=ApiKeyStatus.ACTIVE,
        created_by=user,
    )
    return key, raw_key


def revoke_project_api_key(key: ProjectApiKey) -> ProjectApiKey:
    """Revoke an API key immediately."""
    key.status = ApiKeyStatus.REVOKED
    key.revoked_at = timezone.now()
    key.save(update_fields=["status", "revoked_at"])
    return key


def rotate_project_api_key(
    key: ProjectApiKey,
    *,
    grace_period_hours: int = 24,
    name: str | None = None,
    user=None,
) -> tuple[ProjectApiKey, str, ProjectApiKey]:
    """Rotate an existing key, optionally applying a grace period window."""
    now = timezone.now()
    if grace_period_hours > 0:
        key.status = ApiKeyStatus.GRACE_PERIOD
        key.expires_at = now + timedelta(hours=grace_period_hours)
        key.save(update_fields=["status", "expires_at"])
    else:
        key.status = ApiKeyStatus.REVOKED
        key.revoked_at = now
        key.save(update_fields=["status", "revoked_at"])

    raw_key = generate_api_key()
    new_hash = hash_api_key(raw_key)
    new_prefix = api_key_prefix(raw_key)

    new_key = ProjectApiKey.objects.create(
        project=key.project,
        name=name or key.name,
        api_key_hash=new_hash,
        api_key_prefix=new_prefix,
        scopes=key.scopes,
        status=ApiKeyStatus.ACTIVE,
        created_by=user,
    )

    # Keep project default prefix synced if the rotated key was the project's primary
    if key.project.api_key_hash == key.api_key_hash:
        key.project.api_key_hash = new_hash
        key.project.api_key_prefix = new_prefix
        key.project.save(update_fields=["api_key_hash", "api_key_prefix"])

    return new_key, raw_key, key


def delete_project(project: Project) -> None:
    project.delete()


def integration_snippet(
    raw_key: str, environment: str, base_url: str = "https://api.trazeiq.io"
) -> str:
    """Copy-paste direct HTTP snippet shown next to the raw key, once.

    The integration surface is a plain HTTPS POST — no packaged SDK is
    shipped (see Backend-Phases.md); this snippet is the whole integration.

    ``base_url`` must be the API host the caller reached (scheme + host, no
    trailing slash) — never a hardcoded prod host. In local evaluation the
    view passes ``request.scheme://request.get_host()`` so copying the
    snippet during dev posts to the dev server, not production.
    """
    base = base_url.rstrip("/")
    return (
        f"curl -X POST {base}/api/v1/events/ \\\n"
        '  -H "Content-Type: application/json" \\\n'
        f'  -H "X-API-Key: {raw_key}" \\\n'
        '  -d \'{"environment": "%s", "message": "Hello TrazeIQ"}\'' % environment
    )
