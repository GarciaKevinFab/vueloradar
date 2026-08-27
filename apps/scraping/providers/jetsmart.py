"""Scraper directo de JetSmart Perú.

ESTADO: **verificado en vivo el 2026-08-27** — URL, carga y extracción.
No apareció ningún challenge anti-bot: el deep link sirvió la página completa.
La nota anterior decía lo contrario; el bloqueo del 2026-08-23 no se reprodujo,
así que puede ser intermitente. Si vuelve, el provider devuelve `[]` y deja
screenshot: **no se escribe código para evadir detección de bots.**

El deep link **no aterriza en la lista de vuelos sino en un calendario de
precios**: una grilla de día a precio más barato. Los horarios y el número de
vuelo aparecen recién al hacer clic en un día. Por eso este provider devuelve
**una sola oferta sintética** con el precio del día pedido y sin horarios.
Alcanza para verificar un precio, no para mostrarle vuelos a un usuario.

**Los precios del calendario son TARIFA BASE, no precio final.** La página trae
el enlace "Ver precios con tasas e impuestos", que solo tiene sentido si lo
mostrado va sin ellos. Comprobado contra nuestros propios datos de Google
Flights en 20 fechas de LIM-CUZ: aplicando IGV + TUUA, la diferencia se agrupa
en valores repetidos y exactos (+0,33 en tres fechas, +4,48 en cuatro), lo que
no ocurriría si el calendario ya incluyera impuestos. Por eso
`publishes_base_fare = True`.
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
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
# "Septiembre 2026", "SETIEMBRE", "octubre 2026" — con o sin año.
_MONTH_RE = re.compile(
    r"^\s*(" + "|".join(MESES) + r")\s*(?:de\s*)?\d{0,4}\s*$", re.IGNORECASE
)
_PRICE_RE = re.compile(r"S/\s*([\d.,]+)")


class JetSmartProvider(DirectScraperProvider):
    source_name = "jetsmart"
    airline_name = "JetSMART"
    #: El calendario publica TARIFA BASE (ver el docstring del módulo).
    #: `DirectScraperProvider.search()` le aplica IGV + TUUA antes de devolverla.
    publishes_base_fare = True

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

            precio = price_for_day(texto, date.day, date.month)
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


def price_for_day(texto: str, dia: int, mes: int | None = None):
    """Precio del calendario para el día pedido.

    El calendario muestra dos meses, así que un número de día aparece dos veces
    (el 6 de setiembre y el 6 de octubre). Antes se tomaba la primera aparición
    y se rezaba: con eso, verificar el precio del 6/10 podía traer el del 6/09 y
    disparar una alerta falsa.

    Ahora se sigue el encabezado de mes que el propio calendario imprime. Si no
    hay encabezado y el día está repetido, **devuelve None**: para verificar un
    precio, no saber es mucho mejor que adivinar mal.

    Args:
        texto: innerText del calendario.
        dia: número de día buscado.
        mes: mes buscado (1-12). Sin esto solo se puede resolver el caso en que
            el día aparece una sola vez.
    """
    lineas = [l.strip() for l in (texto or "").splitlines() if l.strip()]

    encontrados = []          # (mes_detectado | None, precio)
    mes_actual = None

    for i, linea in enumerate(lineas):
        nombre = _MONTH_RE.match(linea)
        if nombre:
            mes_actual = MESES[nombre.group(1).lower()]
            continue

        match = _DAY_RE.match(linea)
        if not match or int(match.group(1)) != dia:
            continue

        # El precio va en la línea siguiente, a veces con "Mejor precio" detrás.
        for siguiente in lineas[i + 1 : i + 3]:
            precio_match = _PRICE_RE.search(siguiente)
            if precio_match:
                encontrados.append((mes_actual, parse_price_text(precio_match.group(1))))
                break

    if not encontrados:
        return None

    if mes is not None:
        del_mes = [p for m, p in encontrados if m == mes]
        if len(del_mes) == 1:
            return del_mes[0]
        if del_mes:
            logger.warning(
                "jetsmart: el día %d aparece %d veces dentro del mes %d; no se adivina",
                dia, len(del_mes), mes,
            )
            return None

    if len(encontrados) == 1:
        return encontrados[0][1]

    logger.warning(
        "jetsmart: el día %d aparece %d veces en el calendario y no se pudo "
        "resolver el mes; se descarta el precio", dia, len(encontrados),
    )
    return None


def _accept_cookies(page) -> None:
    try:
        boton = page.query_selector(COOKIE_ACCEPT)
        if boton:
            boton.click()
            page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass
