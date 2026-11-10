from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .api import HealthView

api_v1 = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/", include("apps.users.urls")),
]

# The schema and Swagger UI are exposed only in DEBUG; lead-only access comes with auth (step 3).
if settings.DEBUG:
    api_v1 += [
        path("schema/", SpectacularAPIView.as_view(), name="schema"),
        path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    ]

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include(api_v1)),
]
