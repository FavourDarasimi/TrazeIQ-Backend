from .models import Membership, Organization


def list_organizations_for_user(user):
    """Orgs the user belongs to, newest first."""
    return Organization.objects.filter(memberships__user=user).distinct()


def get_organization_for_user(organization_id: int, user):
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
