"""Proveedor de precios basado en Google Flights (librería `fast-flights`).

Nota de implementación
----------------------
Se usa `fast_flights` para construir la query (`?tfs=` protobuf) y para hacer
el fetch con impersonación de navegador, pero el parseo se hace aquí sobre el
payload JS embebido en la respuesta, no con `fast_flights.parse()`. Dos razones:

1. `parse()` revienta con `IndexError` cuando una fila no trae precio
   (Google muestra "Ver precio" en algunas tarifas de Sky Airline), lo que
   tumbaría la búsqueda entera.
2. Su modelo descarta el número de vuelo, que el dominio necesita para
   deduplicar ofertas entre fuentes.

El payload vive en `<script class="ds:1">` y su forma es un array anidado sin
nombres de campo. Los índices están documentados abajo; si Google los mueve,
este es el único archivo a tocar.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from selectolax.lexbor import LexborHTMLParser

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html

from .. import ratelimit
from ..fx import convert_to_pen
from .base import FlightProvider, PriceParseError, RawFlightOffer, parse_price

logger = logging.getLogger(__name__)

PERU_TZ = ZoneInfo("America/Lima")

# --- Índices del payload de Google Flights (ver docstring del módulo) ---
_IDX_ITINERARIES = 3  # payload[3][0] = itinerarios completos
_LEG_FROM_CODE = 3
_LEG_TO_CODE = 6
_LEG_DEPARTURE_TIME = 8  # [hora, minuto], componentes en cero omitidos
_LEG_ARRIVAL_TIME = 10
_LEG_DEPARTURE_DATE = 20  # [año, mes, día]
_LEG_ARRIVAL_DATE = 21
_LEG_FLIGHT_ID = 22  # [código aerolínea, número de vuelo, _, nombre comercial]


class _SourceThrottle:
    """Espaciado entre consultas a la misma fuente (anti-bloqueo).

    En Fase 1 todo corre en un proceso, así que basta con un lock local. Cuando
    entren los workers de Celery (Fase 2) esto pasa a un lock en Redis para
    garantizar una sola consulta concurrente por fuente.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call: float | None = None

    def wait(self) -> None:
        with self._lock:
            delay = random.uniform(settings.SCRAPE_DELAY_MIN, settings.SCRAPE_DELAY_MAX)
            if self._last_call is not None:
                pending = delay - (time.monotonic() - self._last_call)
                if pending > 0:
                    logger.debug(
                        "google_flights: esperando %.1fs antes de la próxima consulta", pending
                    )
                    time.sleep(pending)
            self._last_call = time.monotonic()


class GoogleFlightsProvider(FlightProvider):
    source_name = "google_flights"

    #: Moneda que se le pide a Google. Los precios vuelven ya convertidos.
    currency = "PEN"
    language = "es"

    _throttle = _SourceThrottle()

    def search(self, origin: str, dest: str, date: date) -> list[RawFlightOffer]:
        """Busca vuelos de un solo tramo. Ante cualquier fallo devuelve `[]`."""
        try:
            query = create_query(
                flights=[
                    FlightQuery(
                        date=date.strftime("%Y-%m-%d"),
                        from_airport=origin,
                        to_airport=dest,
                    )
                ],
                trip="one-way",
                seat="economy",
                passengers=Passengers(adults=1),
                currency=self.currency,
                language=self.language,
            )
            deep_link = query.url()

            self._throttle.wait()
            html = fetch_flights_html(query)
            payload = _extract_payload(html)
            offers = self._build_offers(payload, origin, dest, date, deep_link)
        except Exception:  # noqa: BLE001 - un scraper nunca propaga al caller
            logger.error(
                "google_flights: búsqueda fallida para %s→%s en %s",
                origin,
                dest,
                date.isoformat(),
                exc_info=True,
            )
            # Solo un error real cuenta como fallo de la fuente. Una búsqueda
            # que devuelve vacío es información válida: hay rutas peruanas sin
            # vuelo en muchas fechas, y contarlas como fallo pausaba la fuente
            # en medio del barrido.
            ratelimit.record_failure(self.source_name)
            return []

        ratelimit.record_success(self.source_name)

        if not offers:
            logger.warning(
                "google_flights: 0 ofertas para %s→%s en %s", origin, dest, date.isoformat()
            )
        else:
            logger.info(
                "google_flights: %d ofertas para %s→%s en %s",
                len(offers),
                origin,
                dest,
                date.isoformat(),
            )
        return offers

    # ------------------------------------------------------------------ parsing
    def search_round_trip(
        self, origin: str, dest: str, outbound: date, inbound: date
    ) -> list[RawFlightOffer]:
        """Cotiza el viaje completo como PAQUETE, no como dos tramos sumados.

        Google devuelve, por cada opción de ida, el precio del viaje entero con
        la vuelta más barata compatible. Verificado en vivo el 2026-09-05:
        LIM-CUZ 19/09 + CUZ-LIM 23/09 dio S/ 375 de paquete, con 28 itinerarios
        con precio y el mismo parser de siempre.

        Las ofertas que salen de acá llevan el precio TOTAL del viaje y los
        datos del tramo de ida. **No se persisten nunca**: un precio de paquete
        en el histórico de solo ida contaminaría la serie que sostiene los
        veredictos. Solo se usan para responder al bot.
        """
        try:
            query = create_query(
                flights=[
                    FlightQuery(
                        date=outbound.strftime("%Y-%m-%d"), from_airport=origin, to_airport=dest
                    ),
                    FlightQuery(
                        date=inbound.strftime("%Y-%m-%d"), from_airport=dest, to_airport=origin
                    ),
                ],
                trip="round-trip",
                seat="economy",
                passengers=Passengers(adults=1),
                currency=self.currency,
                language=self.language,
            )
            deep_link = query.url()

            self._throttle.wait()
            html = fetch_flights_html(query)
            payload = _extract_payload(html)
            offers = self._build_offers(payload, origin, dest, outbound, deep_link)
        except Exception:  # noqa: BLE001 - un scraper nunca propaga al caller
            logger.error(
                "google_flights: paquete fallido para %s<->%s %s/%s",
                origin, dest, outbound.isoformat(), inbound.isoformat(), exc_info=True,
            )
            ratelimit.record_failure(self.source_name)
            return []

        ratelimit.record_success(self.source_name)
        logger.info(
            "google_flights: %d paquetes para %s<->%s %s/%s",
            len(offers), origin, dest, outbound.isoformat(), inbound.isoformat(),
        )
        return offers

    def _build_offers(
        self,
        payload: list,
        origin: str,
        dest: str,
        search_date: date,
        deep_link: str,
    ) -> list[RawFlightOffer]:
        offers: list[RawFlightOffer] = []
        for item in _iter_itineraries(payload):
            offer = self._build_offer(item, origin, dest, search_date, deep_link)
            if offer is not None:
                offers.append(offer)
        return offers

    def _build_offer(
        self,
        item: list,
        origin: str,
        dest: str,
        search_date: date,
        deep_link: str,
    ) -> RawFlightOffer | None:
        try:
            itinerary = item[0]
            legs = itinerary[2] or []
            if not legs:
                return None

            price_pen, original_price, original_currency = self._read_price(item)
            if price_pen is None:
                # Google no publicó importe para esta tarifa ("Ver precio").
                return None

            return RawFlightOffer(
                origin=_get(legs[0], _LEG_FROM_CODE) or origin,
                destination=_get(legs[-1], _LEG_TO_CODE) or dest,
                search_date=search_date,
                price_pen=price_pen,
                source=self.source_name,
                airline=self._read_airline(itinerary, legs)[:100],
                flight_number=self._read_flight_number(legs)[:20],
                departure_dt=_read_datetime(legs[0], _LEG_DEPARTURE_DATE, _LEG_DEPARTURE_TIME),
                arrival_dt=_read_datetime(legs[-1], _LEG_ARRIVAL_DATE, _LEG_ARRIVAL_TIME),
                stops=max(len(legs) - 1, 0),
                original_price=original_price,
                original_currency=original_currency,
                deep_link=deep_link,
            )
        except Exception:  # noqa: BLE001 - una fila corrupta no tumba la búsqueda
            logger.warning("google_flights: itinerario ilegible, se omite", exc_info=True)
            return None

    def _read_price(self, item: list) -> tuple[Decimal | None, Decimal | None, str]:
        """Extrae el precio. Devuelve `(None, None, "")` si no hay importe."""
        raw = None
        price_block = _get(item, 1)
        if isinstance(price_block, list) and price_block:
            candidates = price_block[0]
            if isinstance(candidates, list) and len(candidates) > 1:
                raw = candidates[1]

        if raw is None:
            return None, None, ""

        try:
            amount, currency = parse_price(raw, default_currency=self.currency)
        except PriceParseError:
            return None, None, ""

        price_pen = convert_to_pen(amount, currency)
        if price_pen is None:
            # Sin tipo de cambio confiable preferimos perder la oferta antes
            # que guardar un precio calculado con una tasa inventada.
            return None, None, ""
        if currency == "PEN":
            return price_pen, None, ""
        return price_pen, amount, currency

    @staticmethod
    def _read_airline(itinerary: list, legs: list) -> str:
        names = _get(itinerary, 1)
        if isinstance(names, list) and names:
            return " / ".join(str(n) for n in names if n)
        # Fallback: nombre comercial del primer tramo.
        flight_id = _get(legs[0], _LEG_FLIGHT_ID)
        if isinstance(flight_id, list) and len(flight_id) > 3 and flight_id[3]:
            return str(flight_id[3])
        return ""

    @staticmethod
    def _read_flight_number(legs: list) -> str:
        numbers = []
        for leg in legs:
            flight_id = _get(leg, _LEG_FLIGHT_ID)
            if isinstance(flight_id, list) and len(flight_id) > 1 and flight_id[0] and flight_id[1]:
                numbers.append(f"{flight_id[0]}{flight_id[1]}")
        return "/".join(numbers)


# ---------------------------------------------------------------------- helpers
def _extract_payload(html: str) -> list:
    """Saca el array de datos del `<script class="ds:1">` de la respuesta."""
    parser = LexborHTMLParser(html)
    script = parser.css_first(r"script.ds\:1")
    if script is None:
        raise RuntimeError(
            "google_flights: no se encontró el payload embebido (¿cambió el HTML de Google?)"
        )

    raw = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if raw.rstrip().endswith("errorHasStatus: true"):
        raise RuntimeError("google_flights: Google respondió con error para esta búsqueda")

    return json.loads(raw)


def _iter_itineraries(payload: list) -> list:
    """Recorre los itinerarios del payload, tolerando bloques ausentes."""
    block = _get(payload, _IDX_ITINERARIES)
    itineraries = _get(block, 0) if isinstance(block, list) else None
    if not isinstance(itineraries, list):
        return []
    return [item for item in itineraries if isinstance(item, list) and item]


def _get(container, index):
    """Acceso indexado tolerante: devuelve `None` en vez de reventar."""
    if isinstance(container, list) and -len(container) <= index < len(container):
        return container[index]
    return None


def _read_time(value) -> tuple[int, int]:
    """Google omite los componentes en cero: `[8]` es 08:00 y `[None, 31]` es 00:31."""
    padded = [*(value or []), None, None]
    return (padded[0] or 0, padded[1] or 0)


def _read_datetime(leg: list, date_index: int, time_index: int) -> datetime | None:
    raw_date = _get(leg, date_index)
    if not isinstance(raw_date, list) or len(raw_date) < 3:
        return None
    hour, minute = _read_time(_get(leg, time_index))
    try:
        return datetime(
            int(raw_date[0]), int(raw_date[1]), int(raw_date[2]), hour, minute, tzinfo=PERU_TZ
        )
    except (TypeError, ValueError):
        return None


def build_search_url(
    legs: list[tuple[str, str, date]],
    *,
    currency: str = "PEN",
    language: str = "es",
) -> str:
    """URL de Google Flights para uno o varios tramos.

    Con dos tramos que sean el mismo par invertido arma una búsqueda de **ida y
    vuelta**, no dos búsquedas sueltas. Eso importa: el precio de paquete suele
    ser más bajo que la suma de dos pasajes de solo ida, así que este link
    lleva al usuario justo donde está el mejor precio.

    Devuelve "" si no se puede construir; el caller debe tolerarlo.
    """
    if not legs:
        return ""

    try:
        flights = [
            FlightQuery(date=fecha.strftime("%Y-%m-%d"), from_airport=o, to_airport=d)
            for o, d, fecha in legs
        ]
        trip = "round-trip" if _is_round_trip(legs) else "one-way"
        query = create_query(
            flights=flights,
            trip=trip,
            seat="economy",
            passengers=Passengers(adults=1),
            currency=currency,
            language=language,
        )
        return query.url()
    except Exception:  # noqa: BLE001 - un link roto no vale tumbar la respuesta
        logger.warning("google_flights: no se pudo armar la URL de %s", legs, exc_info=True)
        return ""


def _is_round_trip(legs: list[tuple[str, str, date]]) -> bool:
    """Dos tramos con el par invertido y la vuelta posterior a la ida."""
    if len(legs) != 2:
        return False
    (o1, d1, f1), (o2, d2, f2) = legs
    return o1 == d2 and d1 == o2 and f2 > f1
