"""Servicio de búsqueda: orquesta proveedores, deduplica y persiste.

Es el único punto por el que el resto del sistema (CLI, bot, tareas Celery)
pide precios. Los proveedores son intercambiables; esta capa no sabe de dónde
salen los datos.
"""

from __future__ import annotations

import logging
from datetime import date as Date
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from apps.flights.models import Airport, FlightOffer, Route

from .providers.base import RawFlightOffer
from .providers.registry import get_providers_for_route

logger = logging.getLogger(__name__)

#: Tope de itinerarios sintéticos vía hub que se devuelven (evita explosión
#: combinatoria: 15 tramos x 15 tramos = 225 pares posibles).
MAX_CONNECTIONS = 15


class UnknownAirportError(ValueError):
    """El código IATA no está en la tabla `airports`."""


def search_and_store(origin: str, dest: str, date: Date) -> list[FlightOffer]:
    """Busca vuelos `origin→dest` para `date`, los guarda y los devuelve.

    Devuelve las ofertas ordenadas por precio. Si la ruta no tiene vuelo
    directo, arma itinerarios con conexión en el hub (LIM): esos vuelven como
    instancias **sin guardar** (`offer.pk is None`); lo que sí se persiste son
    los tramos reales que los componen.

    Raises:
        UnknownAirportError: si algún IATA no existe en la base.
    """
    origin, dest = origin.strip().upper(), dest.strip().upper()
    origin_airport = _get_airport(origin)
    dest_airport = _get_airport(dest)

    route = _get_or_create_route(origin_airport, dest_airport)
    raw_offers = _collect_from_providers(origin, dest, date, route)

    if raw_offers:
        stored = _persist(route, raw_offers)
        return sorted(stored, key=lambda offer: offer.price_pen)

    hub = settings.HUB_AIRPORT
    if origin == hub or dest == hub:
        logger.info("sin ofertas para %s→%s en %s (ruta con hub)", origin, dest, date)
        return []

    logger.info(
        "sin vuelo directo %s→%s en %s: probando conexión vía %s", origin, dest, date, hub
    )
    return _search_via_hub(route, origin, dest, date, hub)


def search_round_trip(origin: str, dest: str, outbound: Date, inbound: Date) -> list[RawFlightOffer]:
    """Precio del viaje completo como paquete. **No se guarda nada.**

    Existe para que el bot deje de sumar dos pasajes sueltos y llame a eso
    «total»: el paquete de ida y vuelta suele costar distinto, y a veces mucho.
    Solo lo cotiza Google Flights —los scrapers directos no arman paquetes— y
    las ofertas vuelven sin persistirse: un precio de paquete dentro del
    histórico de solo ida rompería la serie contra la que se juzga cada precio.

    Raises:
        UnknownAirportError: si algún IATA no existe en la base.
    """
    from .providers.registry import get_active_providers

    origin, dest = origin.strip().upper(), dest.strip().upper()
    _get_airport(origin)
    _get_airport(dest)
    for provider in get_active_providers():
        cotizar = getattr(provider, "search_round_trip", None)
        if cotizar is not None:
            return dedupe_offers(cotizar(origin, dest, outbound, inbound))
    return []


# --------------------------------------------------------------------- lógica pura
def dedupe_offers(offers: list[RawFlightOffer]) -> list[RawFlightOffer]:
    """Colapsa el mismo vuelo físico en una sola oferta: la más barata.

    Identidad = (aerolínea, número de vuelo, hora de salida). Las ofertas sin
    identidad utilizable (sin número de vuelo ni horario) no se colapsan: se
    dejan pasar todas, porque no hay forma de saber si son el mismo vuelo.
    """
    cheapest: dict[tuple, RawFlightOffer] = {}
    unidentified: list[RawFlightOffer] = []

    for offer in offers:
        airline, flight_number, departure = offer.dedupe_key
        if not flight_number and departure is None:
            unidentified.append(offer)
            continue

        key = (airline, flight_number, departure)
        current = cheapest.get(key)
        if current is None or offer.price_pen < current.price_pen:
            cheapest[key] = offer

    return sorted(
        [*cheapest.values(), *unidentified], key=lambda offer: (offer.price_pen, offer.airline)
    )


def combine_via_hub(
    first_legs: list[RawFlightOffer],
    second_legs: list[RawFlightOffer],
    *,
    hub: str | None = None,
    min_connection_minutes: int | None = None,
    limit: int = MAX_CONNECTIONS,
) -> list[RawFlightOffer]:
    """Arma itinerarios `origen→hub→destino` con conexión suficiente.

    Un par es válido si el segundo tramo sale al menos `min_connection_minutes`
    después de que aterriza el primero. El precio del itinerario es la suma de
    ambos tramos.
    """
    hub = hub or settings.HUB_AIRPORT
    minutes = (
        settings.MIN_CONNECTION_MINUTES if min_connection_minutes is None else min_connection_minutes
    )
    min_layover = timedelta(minutes=minutes)

    combos: list[RawFlightOffer] = []
    for first in first_legs:
        if first.arrival_dt is None:
            continue
        for second in second_legs:
            if second.departure_dt is None:
                continue
            if second.departure_dt - first.arrival_dt < min_layover:
                continue

            combos.append(
                RawFlightOffer(
                    origin=first.origin,
                    destination=second.destination,
                    search_date=first.search_date,
                    price_pen=Decimal(first.price_pen) + Decimal(second.price_pen),
                    source=first.source,
                    airline=_join(first.airline, second.airline, " / ")[:100],
                    flight_number=_join(first.flight_number, second.flight_number, "/")[:20],
                    departure_dt=first.departure_dt,
                    arrival_dt=second.arrival_dt,
                    stops=1,
                    deep_link=first.deep_link,
                    legs=[first, second],
                )
            )

    combos.sort(key=lambda offer: (offer.price_pen, offer.departure_dt or offer.search_date))
    return combos[:limit]


# ------------------------------------------------------------------------ internos
def _collect_from_providers(
    origin: str, dest: str, date: Date, route: Route | None = None
) -> list[RawFlightOffer]:
    """Consulta los proveedores que correspondan a la ruta y deduplica."""
    proveedores = get_providers_for_route(route)

    collected: list[RawFlightOffer] = []
    for provider in proveedores:
        collected.extend(provider.search(origin, dest, date))
    return dedupe_offers(collected)


def _search_via_hub(
    route: Route, origin: str, dest: str, date: Date, hub: str
) -> list[FlightOffer]:
    first_legs = _search_leg(origin, hub, date)
    if not first_legs:
        return []

    second_legs = _search_leg(hub, dest, date)
    if not second_legs:
        return []

    combos = combine_via_hub(first_legs, second_legs, hub=hub)
    if not combos:
        logger.info(
            "hay tramos %s→%s→%s pero ninguna conexión cumple los %d min mínimos",
            origin,
            hub,
            dest,
            settings.MIN_CONNECTION_MINUTES,
        )
        return []

    # Sintéticas: se devuelven sin guardar, solo los tramos reales quedan en DB.
    return [_to_model(route, combo, save=False) for combo in combos]


def _search_leg(origin: str, dest: str, date: Date) -> list[RawFlightOffer]:
    """Busca y persiste un tramo real, y devuelve sus ofertas crudas."""
    try:
        leg_route = _get_or_create_route(_get_airport(origin), _get_airport(dest))
    except UnknownAirportError:
        logger.error("tramo %s→%s imposible: aeropuerto desconocido", origin, dest)
        return []

    raw_offers = _collect_from_providers(origin, dest, date)
    if raw_offers:
        _persist(leg_route, raw_offers)
    return raw_offers


def _get_airport(iata: str) -> Airport:
    try:
        return Airport.objects.get(pk=iata.strip().upper())
    except Airport.DoesNotExist as exc:
        raise UnknownAirportError(
            f"El aeropuerto {iata!r} no existe. Corre `manage.py load_airports` "
            f"o revisa el código IATA."
        ) from exc


def _get_or_create_route(origin: Airport, destination: Airport) -> Route:
    if origin.pk == destination.pk:
        raise UnknownAirportError("El origen y el destino no pueden ser el mismo aeropuerto.")

    route, created = Route.objects.get_or_create(
        origin=origin,
        destination=destination,
        defaults={"is_monitored": False, "has_direct_flights": True, "priority": Route.PRIORITY_LOW},
    )
    if created:
        logger.info("ruta %s creada on-demand (is_monitored=False)", route)
    return route


@transaction.atomic
def _persist(route: Route, raw_offers: list[RawFlightOffer]) -> list[FlightOffer]:
    offers = [_to_model(route, raw, save=False) for raw in raw_offers]
    return FlightOffer.objects.bulk_create(offers)


def _to_model(route: Route, raw: RawFlightOffer, *, save: bool = False) -> FlightOffer:
    offer = FlightOffer(
        route=route,
        airline=raw.airline or "",
        flight_number=raw.flight_number or "",
        departure_dt=raw.departure_dt,
        arrival_dt=raw.arrival_dt,
        stops=raw.stops,
        price_pen=raw.price_pen,
        original_price=raw.original_price,
        original_currency=raw.original_currency or "",
        source=raw.source,
        deep_link=raw.deep_link or "",
        search_date=raw.search_date,
    )
    if save:
        offer.save()
    return offer


def _join(left: str, right: str, separator: str) -> str:
    parts = [part for part in (left, right) if part]
    return separator.join(parts)
