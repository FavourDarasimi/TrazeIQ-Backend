from django.db import transaction

from .models import Membership, MembershipRole, Organization


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