"""Read-side queries for alerts — every queryset is membership-scoped
(Agent.md rule 2)."""

from uuid import UUID

from django.db.models import QuerySet

from .models import AlertLog, AlertRule


def _rules_for_user(user) -> QuerySet[AlertRule]:
    return AlertRule.objects.filter(
        project__organization__memberships__user=user
    ).distinct()


def list_rules_for_user(
    user, *, project_id: UUID | None = None
) -> QuerySet[AlertRule]:
    """Alert rules in the caller's organizations, newest first."""
    qs = _rules_for_user(user).select_related("project")
    if project_id:
        qs = qs.filter(project_id=project_id)
    return qs


def get_rule_for_user(rule_id: UUID, user):
    """A single rule the user can access, or ``None`` — unknown and
    foreign ids resolve to ``None`` and surface as 404."""
    return (
        _rules_for_user(user).select_related("project").filter(id=rule_id).first()
    )


def list_logs_for_user(
    user,
    *,
    rule_id: UUID | None = None,
    incident_id: UUID | None = None,
) -> QuerySet[AlertLog]:
    """Dispatch logs in the caller's organizations, newest first.

    ``rule_id`` / ``incident_id`` narrow to a tenant-scoped pair — passing a
    foreign rule or incident id simply yields an empty list, never a leak.
    """
    qs = (
        AlertLog.objects.filter(
            rule__project__organization__memberships__user=user,
            incident__project__organization__memberships__user=user,
        )
        .select_related("rule", "incident", "incident__error_group")
        .distinct()
    )
    if rule_id:
        qs = qs.filter(rule_id=rule_id)
    if incident_id:
        qs = qs.filter(incident_id=incident_id)
    return qs