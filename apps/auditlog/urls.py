from django.urls import path

from . import views

app_name = "auditlog"

urlpatterns = [
    path("", views.AuditLogListView.as_view(), name="list"),
]
