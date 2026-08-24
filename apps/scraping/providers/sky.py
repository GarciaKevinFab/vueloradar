"""Scraper directo de Sky Airline Perú.

ATENCIÓN: **el precio que publica Sky en el listado es TARIFA BASE, sin
impuestos.** La tarjeta dice literalmente "+ Tasas e impuestos". Eso choca con
la regla del dominio (CLAUDE.md secc. 3: siempre precio final con impuestos),
así que este provider **no sirve para comparar contra Google Flights en
términos absolutos**: siempre va a parecer 20-30% más barato. Sirve para
confirmar que el vuelo existe y para comparar precios entre fechas.

Por eso `VERIFY_DEALS_WITH_DIRECT_SCRAPER` viene en False: activarlo sin
resolver primero el tema de los impuestos corrompería las alertas.

Los selectores se van a romper cuando Sky cambie su web. Están todos juntos
arriba con la fecha en que se verificaron. Ante un fallo el scraper deja un
screenshot en `DIRECT_SCRAPER_SCREENSHOT_DIR`: abrirlo suele bastar para ver
si cambió el DOM o si simplemente no hay vuelos ese día.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings

from .base import RawFlightOffer
from .playwright_base import DirectScraperProvider, parse_price_text

logger = logging.getLogger(__name__)

PERU_TZ = ZoneInfo("America/Lima")

# --- Verificado en vivo el 2026-08-23 -----------------------------------------
# El buscador público (skyairline.com/es/peru) es una SPA sin URL de resultados;
# el motor de venta sí acepta deep link y es el que se usa acá.
SEARCH_URL = (
    "https://initial-sale.skyairline.com/es/peru"
    "?origin={origin}&destination={destination}&departureDate={date}"
    "&flightType=OW&ADT=1"
)
COOKIE_ACCEPT = "button#onetrust-accept-btn-handler"
FLIGHT_CARD = ".itinerary-off-selected-desktop-retail, .cards-flights"
# -----------------------------------------------------------------------------

# El texto de cada tarjeta tiene esta forma:
#   QUEDAN 6 ASIENTOS A ESTE PRECIO / 06:15 / LIM / DIRECTO / 1H 45M / 08:00 /
#   CUZ / Operado por ... / USD 42 | S/ 141,76 / + Tasas e impuestos
# Leer el innerText con regex resiste mejor los cambios de clases que colgarse
# de sub-selectores internos, que en este motor son generados.
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_PEN_RE = re.compile(r"S/\s*([\d.,]+)")
_USD_RE = re.compile(r"USD\s*([\d.,]+)")
_STOPS_RE = re.compile(r"\b(\d+)\s*ESCALA", re.IGNORECASE)


class SkyProvider(DirectScraperProvider):
    source_name = "sky"
    airline_name = "Sky Airline"

    def _search(self, origin: str, dest: str, date: date) -> list[RawFlightOffer]:
        url = SEARCH_URL.format(
            origin=origin.upper(), destination=dest.upper(), date=date.isoformat()
        )

        with self.browser_page() as page:
            logger.info("sky: abriendo %s", url)
            page.goto(url, wait_until="domcontentloaded")
            _accept_cookies(page)

            try:
                page.wait_for_selector(FLIGHT_CARD, timeout=settings.DIRECT_SCRAPER_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                self.save_failure_screenshot(page, origin, dest, date)
                logger.warning("sky: sin resultados visibles para %s→%s en %s", origin, dest, date)
                return []

            tarjetas = page.query_selector_all(FLIGHT_CARD)
            logger.info("sky: %d tarjetas encontradas", len(tarjetas))

            ofertas = []
            for tarjeta in tarjetas:
                oferta = self._read_card(tarjeta, origin, dest, date, url)
                if oferta is not None:
                    ofertas.append(oferta)

            if tarjetas and not ofertas:
                self.save_failure_screenshot(page, origin, dest, date)
                logger.warning("sky: había tarjetas pero ninguna se pudo leer")

            return ofertas

    def _read_card(self, tarjeta, origin, dest, flight_date, url) -> RawFlightOffer | None:
        try:
            texto = (tarjeta.inner_text() or "").strip()
        except Exception:  # noqa: BLE001
            return None
        if not texto:
            return None

        precio, moneda = _read_price(texto)
        if precio is None:
            return None

        horas = _TIME_RE.findall(texto)
        salida = _as_datetime(flight_date, horas[0]) if len(horas) >= 1 else None
        llegada = _as_datetime(flight_date, horas[1]) if len(horas) >= 2 else None

        return RawFlightOffer(
            origin=origin.upper(),
            destination=dest.upper(),
            search_date=flight_date,
            price_pen=precio,
            source=self.source_name,
            airline=self.airline_name,
            # El listado no muestra el número de vuelo; aparece al elegir tarifa.
            flight_number="",
            departure_dt=salida,
            arrival_dt=llegada,
            stops=_read_stops(texto),
            original_price=None if moneda == "PEN" else precio,
            original_currency="" if moneda == "PEN" else moneda,
            deep_link=url,
        )


def _read_price(texto: str) -> tuple[Decimal | None, str]:
    """Prefiere el precio en soles; si solo hay dólares, los convierte.

    Sky suele mostrar ambos ("USD 42 | S/ 141,76"), pero no siempre: según la
    moneda de sesión aparece solo uno.
    """
    match = _PEN_RE.search(texto)
    if match:
        precio = parse_price_text(match.group(1))
        if precio is not None:
            return precio, "PEN"

    match = _USD_RE.search(texto)
    if match:
        crudo = parse_price_text(match.group(1))
        if crudo is not None:
            from ..fx import convert_to_pen

            return convert_to_pen(crudo, "USD"), "USD"

    return None, ""


def _read_stops(texto: str) -> int | None:
    plano = texto.upper()
    if "DIRECTO" in plano or "SIN ESCALA" in plano:
        return 0
    match = _STOPS_RE.search(plano)
    return int(match.group(1)) if match else None


def _accept_cookies(page) -> None:
    """El banner tapa los resultados; si no está, seguimos igual."""
    try:
        boton = page.query_selector(COOKIE_ACCEPT)
        if boton:
            boton.click()
            page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass


def _as_datetime(flight_date: date, hora: tuple[str, str]) -> datetime | None:
    try:
        return datetime(
            flight_date.year, flight_date.month, flight_date.day,
            int(hora[0]), int(hora[1]), tzinfo=PERU_TZ,
        )
    except (TypeError, ValueError):
        return None
