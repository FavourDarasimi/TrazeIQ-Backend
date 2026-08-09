from django.conf import settings
from django.db import models
from django.utils import timezone

from trazeiq_backend.models import UUIDModel


class Organization(UUIDModel):
    """A tenant. Every project and every piece of tenant data hangs off it."""

    name = models.CharField(max_length=120)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_organizations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class MembershipRole(models.TextChoices):
    OWNER = "owner", "owner"
    ADMIN = "admin", "admin"
    DEVELOPER = "developer", "developer"
    VIEWER = "viewer", "viewer"


class Membership(UUIDModel):
    """Links a user to an organization with a role."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=MembershipRole.choices,
        default=MembershipRole.VIEWER,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="uq_membership_user_org",
            )
        ]
        indexes = [
            models.Index(fields=["organization"], name="idx_membership_org"),
        ]

    def __str__(self):
        return f"{self.user.email} @ {self.organization.name} ({self.role})"


class Invite(UUIDModel):
    """A pending team invitation, addressed by email.

    Only the token's hash is stored; the raw token is returned to the inviter
    exactly once and must be presented by the invitee at
    ``POST /api/v1/invites/{token}/accept/`` (Agent.md rule 4). Acceptance
    creates a ``Membership`` with the invited role; the token is single-use.
    """

    email = models.EmailField()
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    role = models.CharField(max_length=16, choices=MembershipRole.choices)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="issued_invites",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization"], name="idx_invite_org"),
            models.Index(
                fields=["email", "organization"],
                name="idx_invite_email_org",
            ),
        ]

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"Invite {self.email} → {self.organization.name} ({self.role})"
