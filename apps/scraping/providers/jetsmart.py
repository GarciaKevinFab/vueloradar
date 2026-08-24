"""Scraper directo de JetSmart Perú.

ESTADO: **la URL está verificada, la extracción NO.** Verificá esto en vivo
antes de habilitar el flag en producción.

Dos cosas hacen a JetSmart más difícil que Sky:

1. `booking.jetsmart.com` está detrás de un challenge anti-bot (aparece un
   "Client Challenge" antes de servir la página). Con un navegador real y las
   opciones de `playwright_base` lo pasa, pero es frágil y puede endurecerse.
2. El deep link **no aterriza en la lista de vuelos sino en un calendario de
   precios**: una grilla de día a precio más barato. Los horarios y el número
   de vuelo aparecen recién al hacer clic en un día.

Por eso este provider devuelve **una sola oferta sintética** con el precio más
barato del día pedido y sin horarios. Alcanza para verificar un precio, no para
mostrarle vuelos a un usuario.

A favor: los precios del calendario **sí incluyen impuestos** (la página tiene
el toggle "Ver precios con tasas e impuestos" y cotiza en soles), a diferencia
de Sky, cuyo listado es tarifa base.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from django.conf import settings

from .base import RawFlightOffer
from .playwright_base import DirectScraperProvider, parse_price_text

logger = logging.getLogger(__name__)

# --- URL verificada en vivo el 2026-08-23 -------------------------------------
# Sale de los enlaces de oferta del home de jetsmart.com/pe/es.
# `r=false` es solo ida; `cur=PEN` fuerza soles.
SEARCH_URL = (
    "https://booking.jetsmart.com/Flight/InternalSelect"
    "?c=true&mon=true&cur=PEN&culture=es-PE"
    "&dd1={date}&o1={origin}&d1={destination}&r=false&ADT=1"
)
COOKIE_ACCEPT = "button#onetrust-accept-btn-handler"
# El calendario es lo primero que renderiza el motor.
CALENDAR_READY = "text=/S\\/\\s?\\d/"
# -----------------------------------------------------------------------------

# El calendario se lee como texto: "6 / S/141.16 / 7 / S/134.44 / Mejor precio".
# Colgarse del innerText resiste mejor que los selectores, que son generados.
_DAY_RE = re.compile(r"^\s*(\d{1,2})\s*$")
_PRICE_RE = re.compile(r"S/\s*([\d.,]+)")


class JetSmartProvider(DirectScraperProvider):
    source_name = "jetsmart"
    airline_name = "JetSMART"

    def _search(self, origin: str, dest: str, date: date) -> list[RawFlightOffer]:
        url = SEARCH_URL.format(
            origin=origin.upper(), destination=dest.upper(), date=date.isoformat()
        )

        with self.browser_page() as page:
            logger.info("jetsmart: abriendo %s", url)
            page.goto(url, wait_until="domcontentloaded")
            _accept_cookies(page)

            try:
                page.wait_for_selector(CALENDAR_READY, timeout=settings.DIRECT_SCRAPER_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                self.save_failure_screenshot(page, origin, dest, date)
                logger.warning(
                    "jetsmart: no apareció el calendario para %s a %s en %s "
                    "(posible challenge anti-bot)", origin, dest, date,
                )
                return []

            try:
                texto = page.inner_text("body")
            except Exception:  # noqa: BLE001
                self.save_failure_screenshot(page, origin, dest, date)
                return []

            precio = price_for_day(texto, date.day)
            if precio is None:
                self.save_failure_screenshot(page, origin, dest, date)
                logger.warning(
                    "jetsmart: calendario visible pero sin precio para el día %d", date.day
                )
                return []

            logger.info("jetsmart: S/ %s para %s a %s el %s", precio, origin, dest, date)
            return [
                RawFlightOffer(
                    origin=origin.upper(),
                    destination=dest.upper(),
                    search_date=date,
                    price_pen=precio,
                    source=self.source_name,
                    airline=self.airline_name,
                    # El calendario da el precio del día, no vuelos individuales.
                    flight_number="",
                    departure_dt=None,
                    arrival_dt=None,
                    stops=None,
                    deep_link=url,
                )
            ]


def price_for_day(texto: str, dia: int):
    """Precio del calendario para el número de día pedido.

    Ojo: el calendario cruza meses, así que un número de día puede aparecer dos
    veces. Se toma la primera aparición, que corresponde al mes mostrado. Es la
    principal razón por la que este provider necesita verificación en vivo.
    """
    lineas = [l.strip() for l in (texto or "").splitlines() if l.strip()]

    for i, linea in enumerate(lineas):
        match = _DAY_RE.match(linea)
        if not match or int(match.group(1)) != dia:
            continue
        # El precio va en la línea siguiente, a veces con "Mejor precio" detrás.
        for siguiente in lineas[i + 1 : i + 3]:
            precio_match = _PRICE_RE.search(siguiente)
            if precio_match:
                return parse_price_text(precio_match.group(1))

    return None


def _accept_cookies(page) -> None:
    try:
        boton = page.query_selector(COOKIE_ACCEPT)
        if boton:
            boton.click()
            page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass
