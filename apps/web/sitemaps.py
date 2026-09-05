"""Sitemap de las páginas públicas.

Cada ficha de ruta se actualiza con cada barrido, así que declaramos
`changefreq` diario: le dice a Google que vuelva, que es justo lo que
diferencia estas páginas del contenido estático de la competencia.
"""

from __future__ import annotations

from django.db.models import Max
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.flights.models import RouteStats

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


def _ultimo_calculo() -> "date | None":
    """Cuándo se recalcularon las estadísticas por última vez.

    Es la fecha de cambio real de todo lo que deriva del histórico: la portada,
    el buscador y la página de cuándo comprar se rehacen con cada barrido.
    """
    return RouteStats.objects.aggregate(ultimo=Max("updated_at"))["ultimo"]


class StaticSitemap(Sitemap):
    """Páginas que se rehacen con cada barrido.

    Llevan `lastmod` porque `changefreq` solo, sin fecha, es una declaración de
    intenciones que Google atiende poco: la señal que sí usa para decidir si
    vuelve es cuándo cambió la página de verdad.
    """

    changefreq = "daily"
    priority = 1.0

    def items(self):
        return ["web:home", "web:buscar", "web:cuando_comprar",
                "web:como_medimos", "web:acerca"]

    def location(self, name):
        return reverse(name)

    def lastmod(self, name):
        return _ultimo_calculo()


class LegalSitemap(Sitemap):
    """Términos y privacidad: texto que no cambia con los precios.

    Van aparte justamente para NO heredar `changefreq="daily"`. Declarar que
    cambian a diario cuando llevan meses iguales gasta rastreo en páginas que
    no lo necesitan, y le resta credibilidad a la señal en las que sí.
    """

    changefreq = "yearly"
    priority = 0.3

    def items(self):
        return ["web:terminos", "web:privacidad"]

    def location(self, name):
        return reverse(name)


class CitySitemap(Sitemap):
    """Páginas por ciudad de origen, salvo las que no son un índice de nada.

    Trece de las dieciocho ciudades tienen un solo destino publicado, así que
    su hub es un enlace a una ficha que ya existe y con menos información que
    ella. Publicarlos era pedirle a Google que indexara duplicados de las
    fichas propias.
    """

    changefreq = "daily"
    priority = 0.9

    def items(self):
        # El lastmod de cada ciudad sale de las rutas que esa página muestra,
        # así que se arma una sola vez acá en vez de una consulta por ciudad.
        self._ultimo_por_ciudad = {}
        for r in queries.published_routes():
            stats = getattr(r, "stats", None)
            if stats is None or stats.updated_at is None:
                continue
            previo = self._ultimo_por_ciudad.get(r.origin_id)
            if previo is None or stats.updated_at > previo:
                self._ultimo_por_ciudad[r.origin_id] = stats.updated_at
        return [a for a in queries.cities_with_routes()
                if queries.hub_indexable(queries.routes_from(a))]

    def location(self, airport):
        return reverse("web:hub", args=[airport.slug])

    def lastmod(self, airport):
        return self._ultimo_por_ciudad.get(airport.iata_code)
