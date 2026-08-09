import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.utils import hash_code

from .models import Invite, Membership, MembershipRole, Organization


def create_organization(name: str, owner):
    """Create an org and make the creator its owner membership in one step."""
    with transaction.atomic():
        organization = Organization.objects.create(name=name, owner=owner)
        Membership.objects.create(
            user=owner,
            organization=organization,
            role=MembershipRole.OWNER,
        )
    return organization


def create_invite(
    *, email: str, role: str, organization: Organization, invited_by
) -> tuple[Invite, str] | None:
    """Issue a pending invite for an address, returning ``(invite, raw_token)``.

    Returns ``None`` when the address already belongs to the organization —
    there is nothing to invite. Re-inviting an address with a pending invite
    rotates it: the old token is voided and a fresh one issued. Only the
    token's hash is persisted; the raw token is shown exactly once.
    """
    email = email.strip().lower()
    if Membership.objects.filter(
        user__email=email, organization=organization
    ).exists():
        return None

    Invite.objects.filter(
        email=email, organization=organization, used_at__isnull=True
    ).update(used_at=timezone.now())

    raw_token = secrets.token_urlsafe(32)
    invite = Invite.objects.create(
        email=email,
        organization=organization,
        role=role,
        invited_by=invited_by,
        token_hash=hash_code(raw_token),
        expires_at=timezone.now()
        + timedelta(minutes=settings.INVITE_TTL_MINUTES),
    )
    return invite, raw_token


def accept_invite(
    *, token: str, user
) -> tuple[str, Membership | None]:
    """Claim an invite token as the logged-in invitee.

    Returns ``(reason, membership)`` where reason is one of
    ``verified|invalid|used|expired|wrong_user|already_member``. On
    ``verified`` the invite is consumed and the ``Membership`` created with
    the invited role. The invite's email must match the accepting user's
    email — a forwarded link must not be claimable by someone else.
    """
    invite = (
        Invite.objects.filter(token_hash=hash_code(token))
        .select_related("organization")
        .first()
    )
    if invite is None:
        return "invalid", None
    if invite.used_at is not None:
        return "used", None
    if invite.is_expired:
        return "expired", None
    if user.email.lower() != invite.email.lower():
        return "wrong_user", None
    if Membership.objects.filter(
        user=user, organization=invite.organization
    ).exists():
        return "already_member", None

    membership = Membership.objects.create(
        user=user,
        organization=invite.organization,
        role=invite.role,
    )
    invite.used_at = timezone.now()
    invite.save(update_fields=["used_at"])
    return "verified", membership