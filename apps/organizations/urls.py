from django.urls import path

from . import views

app_name = "organizations"

urlpatterns = [
    path("", views.OrganizationListView.as_view(), name="list"),
    path("<uuid:pk>/", views.OrganizationDetailView.as_view(), name="detail"),
]
