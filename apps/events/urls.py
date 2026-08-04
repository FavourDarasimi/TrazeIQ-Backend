from django.urls import path

from .views import EventDetailView, EventIngestAndListView

urlpatterns = [
    path("", EventIngestAndListView.as_view(), name="event-ingest"),
    path("<int:event_id>/", EventDetailView.as_view(), name="event-detail"),
]