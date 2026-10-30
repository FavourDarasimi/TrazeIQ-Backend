import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from trazeiq_backend.models import UUIDModel


class Project(UUIDModel):
    """An application watched by TrazeIQ.

    The raw API key is never stored — only its deterministic HMAC-SHA256
    digest, which the ingestion pipeline (Phase 1D) re-computes from the
    ``X-API-Key`` header to find the project.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(max_length=120)
    api_key_hash = models.CharField(max_length=64, db_index=True)
    api_key_prefix = models.CharField(max_length=16, editable=False)
    environment = models.CharField(max_length=32, default="production")
    events_per_minute = models.PositiveIntegerField(
        default=1000,
        help_text=(
            "Per-project ingestion cap (events/minute). Enforced by "
            "EventPerKeyRateThrottle; overrides the global EVENT_THROTTLE_KEY."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization"], name="idx_project_org"),
        ]

    def __str__(self):
        return f"{self.name} ({self.api_key_prefix}...)"


class ApiKeyScope(models.TextChoices):
    INGEST_ONLY = "ingest_only", "Ingest only"
    READ_ONLY = "read_only", "Read only"
    SOURCEMAPS_UPLOAD = "sourcemaps_upload", "Sourcemaps upload"
    FULL_ACCESS = "full_access", "Full access"


class ApiKeyStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    GRACE_PERIOD = "grace_period", "Grace period"
    REVOKED = "revoked", "Revoked"


class ProjectApiKey(UUIDModel):
    """A scoped credential granting access to a Project.

    Projects support multiple active API keys with fine-grained scopes
    (ingest_only, read_only, sourcemaps_upload), rotation with configurable
    grace periods, last-used tracking, and request volume auditing.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=120)
    api_key_hash = models.CharField(max_length=64, db_index=True, unique=True)
    api_key_prefix = models.CharField(max_length=16, editable=False)
    scopes = models.JSONField(
        default=list,
        help_text="Allowed permissions: ingest_only, read_only, sourcemaps_upload, full_access.",
    )
    status = models.CharField(
        max_length=16,
        choices=ApiKeyStatus.choices,
        default=ApiKeyStatus.ACTIVE,
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    request_count = models.PositiveBigIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_api_keys",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["project", "status"],
                name="idx_proj_apikey_proj_status",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.api_key_prefix}...) [{self.status}]"

    @property
    def is_valid(self) -> bool:
        if self.status == ApiKeyStatus.REVOKED:
            return False
        if self.status == ApiKeyStatus.GRACE_PERIOD:
            if self.expires_at and timezone.now() >= self.expires_at:
                return False
        return True

    def has_scope(self, scope: str) -> bool:
        if not self.scopes:
            return False
        return ApiKeyScope.FULL_ACCESS in self.scopes or scope in self.scopes

