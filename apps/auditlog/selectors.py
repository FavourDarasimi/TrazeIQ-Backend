from apps.organizations.models import Membership, MembershipRole

from .models import AuditLog


def list_audit_logs_for_user(user, organization_id=None):
    """Audit logs visible to ``user``.

    Without ``organization_id`` the caller sees logs across every organization
    they administer (owner/admin). With ``organization_id`` the result is
    scoped to that single org (the view must already have authorized access).
    """
    if organization_id is not None:
        return AuditLog.objects.filter(organization_id=organization_id).order_by(
            "-created_at"
        )
    admin_org_ids = Membership.objects.filter(
        user=user, role__in=[MembershipRole.OWNER, MembershipRole.ADMIN]
    ).values_list("organization_id", flat=True)
    return AuditLog.objects.filter(
        organization_id__in=list(admin_org_ids)
    ).order_by("-created_at")
