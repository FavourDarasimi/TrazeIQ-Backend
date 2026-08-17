from django.urls import path

from .views import ServicesHealthView

urlpatterns = [
    path("health/", ServicesHealthView.as_view(), name="services-health"),
]