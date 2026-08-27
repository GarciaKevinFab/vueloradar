"""Sitemap de las páginas públicas.

Cada ficha de ruta se actualiza con cada barrido, así que declaramos
`changefreq` diario: le dice a Google que vuelva, que es justo lo que
diferencia estas páginas del contenido estático de la competencia.
"""

from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from . import queries


class RouteSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8

    def items(self):
        return list(queries.published_routes())

    def location(self, route):
        return reverse("web:route", args=[route.origin_id, route.destination_id])

    def lastmod(self, route):
        stats = getattr(route, "stats", None)
        return stats.updated_at if stats else None


class StaticSitemap(Sitemap):
    changefreq = "daily"
    priority = 1.0

    def items(self):
        return ["web:home", "web:terminos", "web:privacidad"]

    def location(self, name):
        return reverse(name)


class CitySitemap(Sitemap):
    """Páginas por ciudad de origen."""

    changefreq = "daily"
    priority = 0.9

    def items(self):
        return queries.cities_with_routes()

    def location(self, airport):
        return reverse("web:hub", args=[airport.slug])
