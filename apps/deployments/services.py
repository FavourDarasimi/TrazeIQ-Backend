import logging
import re
from uuid import UUID

from django.utils import timezone

from apps.incidents.models import Incident
from apps.incidents.services import verify_incident_fix
from apps.projects.models import Project

from .models import Deployment, Release

logger = logging.getLogger(__name__)

# Patterns like "fixes #123", "closes #45", "fix: #uuid"
_FIX_PATTERN = re.compile(
    r"(?:fix(?:es)?|close[sd]?|resolve[sd]?)\s+#([a-f0-9-]{7,36})",
    re.IGNORECASE,
)


def record_deployment(
    project: Project,
    *,
    release_version: str,
    environment: str = "production",
    status: str = Deployment.Status.COMPLETED,
    commit_sha: str = "",
    commit_message: str = "",
    commit_author: str = "",
    commit_url: str = "",
    pull_request_url: str = "",
    pull_request_title: str = "",
    deployed_at=None,
    metadata: dict | None = None,
    auto_resolve_incidents: bool = False,
    verified_incidents: list[UUID | str] | None = None,
) -> Deployment:
    """Record a deployment marker, ensuring the Release exists and linking incidents.

    If commit messages or verified_incidents list any open/investigating incidents,
    they are automatically transitioned to FIX_VERIFIED (or RESOLVED if auto_resolve=True)
    with timeline deployment evidence attached.
    """
    deployed_at = deployed_at or timezone.now()
    env = environment or project.environment or "production"
    meta = dict(metadata or {})

    # 1. Get or create the Release record
    release, _created = Release.objects.get_or_create(
        project=project,
        version=release_version.strip(),
        defaults={
            "commit_sha": commit_sha.strip(),
            "commit_message": commit_message.strip(),
            "commit_author": commit_author.strip(),
            "commit_url": commit_url.strip(),
            "pull_request_url": pull_request_url.strip(),
            "pull_request_title": pull_request_title.strip(),
            "metadata": meta,
        },
    )

    # If the release existed, populate any missing git metadata provided by this deploy
    update_fields = []
    for field, val in [
        ("commit_sha", commit_sha.strip()),
        ("commit_message", commit_message.strip()),
        ("commit_author", commit_author.strip()),
        ("commit_url", commit_url.strip()),
        ("pull_request_url", pull_request_url.strip()),
        ("pull_request_title", pull_request_title.strip()),
    ]:
        if val and not getattr(release, field):
            setattr(release, field, val)
            update_fields.append(field)
    if update_fields:
        release.save(update_fields=update_fields)

    # 2. Create the Deployment instance
    deployment = Deployment.objects.create(
        project=project,
        release=release,
        environment=env,
        status=status,
        deployed_at=deployed_at,
        metadata=meta,
    )

    # 3. Check for incident fixes (either explicit IDs or extracted from commit message)
    incident_ids = set()
    if verified_incidents:
        for inc_id in verified_incidents:
            incident_ids.add(str(inc_id).strip())

    if commit_message:
        for match in _FIX_PATTERN.finditer(commit_message):
            incident_ids.add(match.group(1).strip())

    if incident_ids:
        # Match valid UUID incidents in this project that are not yet resolved
        candidate_incidents = Incident.objects.filter(
            project=project,
        ).exclude(status=Incident.Status.RESOLVED)

        for candidate in candidate_incidents:
            cand_id_str = str(candidate.pk)
            # Match either full UUID or 8-char short prefix
            if cand_id_str in incident_ids or cand_id_str[:8] in incident_ids:
                try:
                    verify_incident_fix(
                        candidate,
                        release=release.version,
                        commit_sha=release.commit_sha,
                        notes="Automated verification from deploy marker",
                        auto_resolve=auto_resolve_incidents,
                    )
                except Exception as exc:
                    logger.warning("Failed to verify incident %s on deploy: %s", candidate.pk, exc)

    logger.info(
        "Deployment recorded project=%s release=%s env=%s status=%s",
        project.pk,
        release.version,
        env,
        status,
    )
    return deployment


def process_github_webhook(
    project: Project,
    payload: dict,
    event_type: str = "deployment_status",
) -> Deployment | None:
    """Parse and record GitHub webhook deployments, releases, or pushes."""
    if event_type == "deployment_status":
        dep = payload.get("deployment") or {}
        dep_status = payload.get("deployment_status") or {}
        state = dep_status.get("state")
        status_map = {
            "success": Deployment.Status.COMPLETED,
            "failure": Deployment.Status.FAILED,
            "error": Deployment.Status.FAILED,
            "in_progress": Deployment.Status.STARTED,
        }
        status = status_map.get(state, Deployment.Status.COMPLETED)
        environment = dep.get("environment") or "production"
        ref = dep.get("ref") or dep.get("sha", "")[:8] or "latest"
        sha = dep.get("sha", "")
        repo_url = payload.get("repository", {}).get("html_url", "")
        commit_url = f"{repo_url}/commit/{sha}" if repo_url and sha else ""

        return record_deployment(
            project=project,
            release_version=ref,
            environment=environment,
            status=status,
            commit_sha=sha,
            commit_url=commit_url,
            metadata={"github": {"deployment_id": dep.get("id")}},
        )

    if event_type == "release":
        action = payload.get("action")
        if action not in ("published", "released", "created"):
            return None
        rel_data = payload.get("release") or {}
        version = rel_data.get("tag_name") or rel_data.get("name") or "latest"
        sha = rel_data.get("target_commitish", "")
        msg = rel_data.get("body", "") or rel_data.get("name", "")
        author = rel_data.get("author", {}).get("login", "")
        url = rel_data.get("html_url", "")

        return record_deployment(
            project=project,
            release_version=version,
            environment=project.environment or "production",
            status=Deployment.Status.COMPLETED,
            commit_sha=sha,
            commit_message=msg,
            commit_author=author,
            commit_url=url,
            metadata={"github": {"release_id": rel_data.get("id")}},
        )

    if event_type == "push":
        ref = payload.get("ref", "")
        head_commit = payload.get("head_commit") or {}
        sha = head_commit.get("id") or payload.get("after", "")
        version = ref.replace("refs/tags/", "").replace("refs/heads/", "") or sha[:8] or "latest"
        msg = head_commit.get("message", "")
        author = head_commit.get("author", {}).get("name", "")
        url = head_commit.get("url", "")

        return record_deployment(
            project=project,
            release_version=version,
            environment=project.environment or "production",
            status=Deployment.Status.COMPLETED,
            commit_sha=sha,
            commit_message=msg,
            commit_author=author,
            commit_url=url,
            metadata={"github": {"ref": ref}},
        )

    return None


def process_gitlab_webhook(
    project: Project,
    payload: dict,
) -> Deployment | None:
    """Parse and record GitLab webhook deployment or release hooks."""
    object_kind = payload.get("object_kind")

    if object_kind == "deployment":
        state = payload.get("status")
        status_map = {
            "success": Deployment.Status.COMPLETED,
            "failed": Deployment.Status.FAILED,
            "running": Deployment.Status.STARTED,
        }
        status = status_map.get(state, Deployment.Status.COMPLETED)
        environment = payload.get("environment") or "production"
        ref = payload.get("ref") or payload.get("sha", "")[:8] or "latest"
        sha = payload.get("sha", "")
        commit = payload.get("commit") or {}
        msg = commit.get("message", "")
        author = commit.get("author_name", "")
        url = commit.get("url", "")

        return record_deployment(
            project=project,
            release_version=ref,
            environment=environment,
            status=status,
            commit_sha=sha,
            commit_message=msg,
            commit_author=author,
            commit_url=url,
            metadata={"gitlab": {"deployment_id": payload.get("deployment_id")}},
        )

    if object_kind in ("release", "tag_push", "push"):
        version = payload.get("tag") or payload.get("name") or payload.get("ref", "").replace("refs/tags/", "") or "latest"
        commits = payload.get("commits") or []
        head = commits[-1] if commits else {}
        sha = head.get("id") or payload.get("after", "")
        msg = head.get("message", "") or payload.get("description", "")
        author = head.get("author", {}).get("name", "")
        url = head.get("url", "")

        return record_deployment(
            project=project,
            release_version=version,
            environment=project.environment or "production",
            status=Deployment.Status.COMPLETED,
            commit_sha=sha,
            commit_message=msg,
            commit_author=author,
            commit_url=url,
            metadata={"gitlab": {"object_kind": object_kind}},
        )

    return None
