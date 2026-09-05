from django.urls import path

from .views import (
    SLOAlertAcknowledgeView,
    SLOAlertsView,
    SLOBudgetView,
    SLODependencyView,
    SLODetailView,
    SLOHistoryView,
    SLOListView,
)

urlpatterns = [
    path("", SLOListView.as_view(), name="slo-list"),
    path("dependencies/", SLODependencyView.as_view(), name="slo-dependencies"),
    path("<uuid:pk>/", SLODetailView.as_view(), name="slo-detail"),
    path("<uuid:pk>/budget/", SLOBudgetView.as_view(), name="slo-budget"),
    path("<uuid:pk>/history/", SLOHistoryView.as_view(), name="slo-history"),
    path("<uuid:pk>/alerts/", SLOAlertsView.as_view(), name="slo-alerts"),
    path(
        "<uuid:pk>/alerts/<uuid:alert_id>/acknowledge/",
        SLOAlertAcknowledgeView.as_view(),
        name="slo-alert-acknowledge",
    ),
]