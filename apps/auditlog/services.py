from .models import AuditAction, AuditLog


def record_audit_log(*, actor, organization, action, target) -> AuditLog:
    """Write a single audit-log entry.

    Callers supply the acting user, the tenant, the ``AuditAction`` constant
    and a readable ``target`` describing the subject of the action.
    """
    return AuditLog.objects.create(
        actor=actor,
        organization=organization,
        action=action,
        target=target,
    )
