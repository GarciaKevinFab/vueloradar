import os

from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from apps.web.sitemaps import RouteSitemap, StaticSitemap
from apps.web.views import robots_txt

from .health import healthz

# El admin va detrás de un path no obvio en produccion (ver DEPLOY.md).
ADMIN_PATH = os.getenv("DJANGO_ADMIN_PATH", "admin").strip("/")

SITEMAPS = {"static": StaticSitemap, "routes": RouteSitemap}

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("robots.txt", robots_txt, name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path(f"{ADMIN_PATH}/", admin.site.urls),
    path("", include("apps.web.urls")),
]
