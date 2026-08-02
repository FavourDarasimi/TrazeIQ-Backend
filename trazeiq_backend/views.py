"""Health check endpoint for monitoring TrazeIQ itself."""

from django.http import JsonResponse


def health(request):
    return JsonResponse({"status": "ok"})
