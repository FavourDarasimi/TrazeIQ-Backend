from django.urls import path

from .views import AlertLogListView, AlertRuleDetailView, AlertRuleListView

urlpatterns = [
    path("rules/", AlertRuleListView.as_view(), name="alert-rule-list"),
    path(
        "rules/<uuid:pk>/",
        AlertRuleDetailView.as_view(),
        name="alert-rule-detail",
    ),
    path("logs/", AlertLogListView.as_view(), name="alert-log-list"),
]