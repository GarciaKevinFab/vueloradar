import os

from django.contrib import admin
from django.urls import path

from .health import healthz

# El admin va detrás de un path no obvio en produccion (ver DEPLOY.md).
ADMIN_PATH = os.getenv("DJANGO_ADMIN_PATH", "admin").strip("/")

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path(f"{ADMIN_PATH}/", admin.site.urls),
]
