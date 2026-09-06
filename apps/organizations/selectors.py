from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from .models import Invite, Membership, Organization


def list_organizations_for_user(user):
    """Orgs the user belongs to, newest first."""
    return Organization.objects.filter(memberships__user=user).distinct()


def get_organization_for_user(organization_id: UUID, user):
    """A single org the user is a member of, or ``None``.

    Returns ``None`` both for unknown ids and for orgs the caller has no
    membership in — the caller's view turns that into a 404 so tenant
    existence is never leaked.
    """
    return (
        Organization.objects.filter(memberships__user=user, id=organization_id)
        .distinct()
        .first()
    )


def get_membership(user, organization) -> Membership | None:
    return Membership.objects.filter(
        user=user, organization=organization
    ).first()


def list_members(organization) -> QuerySet[Membership]:
    """All members of an org, oldest first. The caller has already proven
    membership in the organization itself."""
    return Membership.objects.filter(organization=organization).order_by(
        "created_at", "id"
    )


def list_pending_invites(organization) -> QuerySet[Invite]:
    """Outstanding invites for an org: never used and not yet expired,
    newest first. The caller must already hold a manager role — invite
    rows expose outsider email addresses, so viewers never see them."""
    return Invite.objects.filter(
        organization=organization,
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at", "-id")
