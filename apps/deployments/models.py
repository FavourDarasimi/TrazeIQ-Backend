from django.db import models
from django.utils import timezone

from trazeiq_backend.models import UUIDModel


class Release(UUIDModel):
    """A distinct code release / version tag for a project.

    Stores version metadata, associated commit SHA and message, author,
    repository commit URL, and pull request information for regression
    investigation.
    """

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="releases",
    )
    version = models.CharField(max_length=128)
    commit_sha = models.CharField(max_length=64, blank=True, default="")
    commit_message = models.TextField(blank=True, default="")
    commit_author = models.CharField(max_length=255, blank=True, default="")
    commit_url = models.URLField(max_length=500, blank=True, default="")
    pull_request_url = models.URLField(max_length=500, blank=True, default="")
    pull_request_title = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version"],
                name="uniq_release_project_version",
            ),
        ]

    def __str__(self):
        return f"{self.project_id} @ {self.version}"


class Deployment(UUIDModel):
    """A rollout of a Release to a specific environment at a point in time.

    Deploy markers ingested from GitHub, GitLab, CLI tools, or SDK calls
    are recorded here to power Deploy Intelligence regression detection.
    """

    class Status(models.TextChoices):
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        HEALTHY = "healthy", "Healthy"
        DEGRADED = "degraded", "Degraded"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="deployments",
    )
    release = models.ForeignKey(
        Release,
        on_delete=models.CASCADE,
        related_name="deployments",
    )
    environment = models.CharField(max_length=32, default="production")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED,
    )
    deployed_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-deployed_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["project", "environment", "-deployed_at"],
                name="idx_deploy_proj_env_time",
            ),
        ]

    def __str__(self):
        return f"{self.release.version} ({self.environment}) - {self.status}"
