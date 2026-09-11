from django.urls import path

from . import views

app_name = "platform"

urlpatterns = [
    path("overview/", views.PlatformOverviewView.as_view(), name="overview"),
    path("users/", views.PlatformUserListView.as_view(), name="users"),
    path(
        "organizations/",
        views.PlatformOrganizationListView.as_view(),
        name="organizations",
    ),
    path("projects/", views.PlatformProjectListView.as_view(), name="projects"),
    path("health/", views.PlatformHealthView.as_view(), name="health"),
]
