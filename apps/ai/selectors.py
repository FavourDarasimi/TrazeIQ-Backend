"""Read-only selectors for AI analysis objects."""

from .models import AIAnalysis


def get_latest_analysis_for_incident(incident_id: int) -> AIAnalysis | None:
    """Return the most recent AIAnalysis object for an incident, or None if none
    exists.
    """
    return (
        AIAnalysis.objects.filter(incident_id=incident_id)
        .order_by("-created_at", "-id")
        .first()
    )
