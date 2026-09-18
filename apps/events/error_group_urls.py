from django.urls import path

from .views import ErrorGroupListView

urlpatterns = [
    path("", ErrorGroupListView.as_view(), name="error-group-list"),
]
