from django.conf import settings
from django.db import models

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
