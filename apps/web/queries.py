"""Lecturas del histórico para las páginas públicas.

Todo lo que la web muestra sale de tablas que ya existían (`PriceSnapshot`,
`RouteStats`): la capa web no scrapea ni escribe nada, solo lee. Las consultas
viven acá para que las vistas queden delgadas y testeables.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Min
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.flights.models import Airport, PriceSnapshot, Route, RouteStats

#: Un snapshot más viejo que esto ya no representa "el precio de hoy".
FRESH_WINDOW = timedelta(hours=36)
#: Ventana del gráfico histórico.
HISTORY_DAYS = 60
#: Cuántas fechas futuras listamos en el calendario de precios.
UPCOMING_DAYS = 60


@dataclass(frozen=True)
class DayPrice:
    """Precio mínimo observado para un día concreto."""

    day: date
    price: Decimal


def fresh_since():
    return timezone.now() - FRESH_WINDOW


def current_min_price(route: Route) -> Decimal | None:
    """Precio mínimo vigente de la ruta, sobre cualquier fecha futura."""
    result = (
        PriceSnapshot.objects
        .filter(route=route, snapshot_at__gte=fresh_since(), flight_date__gte=timezone.localdate())
        .aggregate(m=Min("min_price_pen"))
    )
    return result["m"]


def upcoming_prices(route: Route, days: int = UPCOMING_DAYS) -> list[DayPrice]:
    """Precio vigente por fecha de vuelo, para el calendario de la ruta."""
    today = timezone.localdate()
    rows = (
        PriceSnapshot.objects
        .filter(
            route=route,
            snapshot_at__gte=fresh_since(),
            flight_date__gte=today,
            flight_date__lte=today + timedelta(days=days),
        )
        .values("flight_date")
        .annotate(price=Min("min_price_pen"))
        .order_by("flight_date")
    )
    return [DayPrice(day=r["flight_date"], price=r["price"]) for r in rows]


def price_history(route: Route, days: int = HISTORY_DAYS) -> list[DayPrice]:
    """Serie diaria del precio más barato observado.

    Este es el activo que ningún competidor puede reconstruir hacia atrás:
    lo que realmente costó volar esta ruta, día por día.
    """
    since = timezone.now() - timedelta(days=days)
    rows = (
        PriceSnapshot.objects
        .filter(route=route, snapshot_at__gte=since)
        .annotate(day=TruncDate("snapshot_at"))
        .values("day")
        .annotate(price=Min("min_price_pen"))
        .order_by("day")
    )
    return [DayPrice(day=r["day"], price=r["price"]) for r in rows]


def all_time_low(route: Route) -> PriceSnapshot | None:
    """El precio más barato jamás observado en la ruta."""
    return PriceSnapshot.objects.filter(route=route).order_by("min_price_pen", "snapshot_at").first()


def cheapest_upcoming(prices: list[DayPrice], limit: int = 5) -> list[DayPrice]:
    """Las fechas más baratas del calendario, para el bloque de recomendación."""
    return sorted(prices, key=lambda p: p.price)[:limit]


def published_routes():
    """Rutas que se publican: monitoreadas y con histórico que mostrar."""
    return (
        Route.objects
        .filter(is_monitored=True, snapshots__isnull=False)
        .select_related("origin", "destination", "stats")
        .distinct()
        .order_by("priority", "origin_id", "destination_id")
    )


def stats_for(route: Route) -> RouteStats | None:
    return RouteStats.objects.filter(route=route).first()


# --- versiones masivas -------------------------------------------------------
# La portada necesita lo mismo para 40 rutas. Pedirlo ruta por ruta daba 121
# consultas y 22 segundos: tras cada purga del borde ese costo lo paga el
# primer visitante, que muchas veces es Googlebot. Estas dos funciones traen
# todo en una consulta cada una.

def bulk_upcoming_prices(route_ids, days: int = UPCOMING_DAYS) -> dict[int, list[DayPrice]]:
    """Precio vigente por fecha de vuelo, para todas las rutas a la vez."""
    today = timezone.localdate()
    rows = (
        PriceSnapshot.objects
        .filter(
            route_id__in=route_ids,
            snapshot_at__gte=fresh_since(),
            flight_date__gte=today,
            flight_date__lte=today + timedelta(days=days),
        )
        .values("route_id", "flight_date")
        .annotate(price=Min("min_price_pen"))
        .order_by("route_id", "flight_date")
    )
    agrupado: dict[int, list[DayPrice]] = defaultdict(list)
    for r in rows:
        agrupado[r["route_id"]].append(DayPrice(day=r["flight_date"], price=r["price"]))
    return agrupado


def bulk_price_history(route_ids, days: int = HISTORY_DAYS) -> dict[int, list[DayPrice]]:
    """Serie diaria de mínimos, para todas las rutas a la vez."""
    since = timezone.now() - timedelta(days=days)
    rows = (
        PriceSnapshot.objects
        .filter(route_id__in=route_ids, snapshot_at__gte=since)
        .annotate(day=TruncDate("snapshot_at"))
        .values("route_id", "day")
        .annotate(price=Min("min_price_pen"))
        .order_by("route_id", "day")
    )
    agrupado: dict[int, list[DayPrice]] = defaultdict(list)
    for r in rows:
        agrupado[r["route_id"]].append(DayPrice(day=r["day"], price=r["price"]))
    return agrupado


@dataclass(frozen=True)
class Related:
    """Rutas vecinas de una ficha, para que la autoridad circule.

    Sin esto cada ficha es una hoja huérfana: solo la portada enlaza hacia
    abajo y nada enlaza de lado.
    """

    inverse: object | None
    from_origin: list
    to_destination: list

    @property
    def is_empty(self) -> bool:
        return not (self.inverse or self.from_origin or self.to_destination)


def related_routes(route, limit: int = 4) -> Related:
    """Ruta inversa y vecinas que compartan aeropuerto, en una sola consulta."""
    aeropuertos = {route.origin_id, route.destination_id}
    vecinas = [
        r for r in published_routes()
        if r.pk != route.pk and ({r.origin_id, r.destination_id} & aeropuertos)
    ]
    inversa = next(
        (r for r in vecinas
         if r.origin_id == route.destination_id and r.destination_id == route.origin_id),
        None,
    )
    return Related(
        inverse=inversa,
        from_origin=[r for r in vecinas if r.origin_id == route.origin_id][:limit],
        to_destination=[
            r for r in vecinas
            if r.destination_id == route.destination_id and r is not inversa
        ][:limit],
    )


def cities_with_routes() -> list:
    """Aeropuertos que son origen de al menos una ruta publicada.

    Es la lista que alimenta las páginas por ciudad y los enlaces de la
    portada: sin rutas publicadas, la página quedaría vacía.
    """
    vistos, ciudades = set(), []
    for r in published_routes():
        if r.origin_id not in vistos:
            vistos.add(r.origin_id)
            ciudades.append(r.origin)
    return sorted(ciudades, key=lambda a: a.city)


def airport_by_slug(slug: str):
    """Aeropuerto cuyo slug de ciudad coincide. None si no hay ninguno."""
    return next((a for a in cities_with_routes() if a.slug == slug), None)


def routes_from(airport) -> list:
    """Rutas publicadas que salen de ese aeropuerto."""
    return [r for r in published_routes() if r.origin_id == airport.iata_code]


def total_snapshots() -> int:
    """Cuántos precios llevamos guardados.

    Es el activo del producto —nadie puede reconstruirlo hacia atrás— y hasta
    ahora no aparecía en ningún lado.
    """
    return PriceSnapshot.objects.count()


@dataclass(frozen=True)
class Cobertura:
    """Los números que sostienen lo que el sitio afirma."""

    snapshots: int
    rutas: int
    aeropuertos: int
    desde: date | None
    hasta: date | None

    @property
    def dias_midiendo(self) -> int:
        if not self.desde or not self.hasta:
            return 0
        return (self.hasta - self.desde).days + 1


def cobertura() -> Cobertura:
    """Alcance real de la medición, para poder citarlo en vez de prometerlo.

    Una página de metodología que dice «medimos a diario muchas rutas» no vale
    nada: cualquiera lo escribe. La que dice cuántos precios lleva guardados y
    desde cuándo se puede comprobar contra el propio sitio, y eso es lo que la
    vuelve contenido y no publicidad.

    Va en una sola consulta agregada: son tres números que aparecen juntos y
    pedirlos por separado costaría tres viajes a Supabase por visita.
    """
    agregado = PriceSnapshot.objects.aggregate(
        snapshots=Count("id"), desde=Min("snapshot_at"), hasta=Max("snapshot_at")
    )
    desde, hasta = agregado["desde"], agregado["hasta"]
    return Cobertura(
        snapshots=agregado["snapshots"] or 0,
        rutas=len(published_routes()),
        aeropuertos=Airport.objects.count(),
        desde=timezone.localtime(desde).date() if desde else None,
        hasta=timezone.localtime(hasta).date() if hasta else None,
    )


#: Un hub con un solo destino no es un índice: es un enlace a una ficha que ya
#: existe, con menos información que ella. De las 18 ciudades, 13 están así.
MIN_DESTINOS_HUB = 2


def hub_indexable(rutas) -> bool:
    """Si la página de una ciudad merece competir en el índice por sí sola.

    El criterio es dinámico a propósito: cuando una ciudad gane un segundo
    destino, su hub vuelve a indexarse sin que nadie toque una lista. Y al
    revés, si lo pierde, deja de hacerlo. Una lista de excepciones escrita a
    mano habría envejecido con el primer cambio de rutas.

    No se hace 404 ni redirección: la página sigue sirviendo para quien llegue
    desde el selector de la portada. Lo que se evita es que compita en Google
    contra la ficha que sí tiene el contenido.
    """
    return len(rutas) >= MIN_DESTINOS_HUB


def last_seen(route: Route):
    """Cuándo se observó por última vez un precio de esta ruta.

    La ficha decía «Actualizado» con la hora de render, que es la hora en que
    alguien la miró, no la hora en que se midió el precio. Con el borde
    cacheando media hora y el barrido dos veces al día, la diferencia podía
    ser de doce horas y el lector no tenía forma de saberlo. Esto es lo que
    convierte «no es el precio que vas a pagar» en un dato: hace cuánto se
    observó, para que cada uno juzgue.
    """
    return PriceSnapshot.objects.filter(route=route).aggregate(m=Max("snapshot_at"))["m"]
