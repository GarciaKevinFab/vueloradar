"""Cimientos comunes de los scrapers directos de aerolínea.

Estos providers son el respaldo de Google Flights y el verificador de precios
antes de mandar una alerta. Son pesados (un Chromium por búsqueda) y frágiles
(dependen del DOM de cada aerolínea), así que **no entran al barrido masivo**.

Cuando una aerolínea cambie su HTML, lo único a tocar son las constantes de
selectores al inicio de cada provider.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from ..taxes import base_fare_to_final
from .base import FlightProvider, PriceParseError, RawFlightOffer, parse_price

logger = logging.getLogger(__name__)

#: Un navegador real de escritorio en Perú. Un viewport de 800x600 o un locale
#: en inglés son señales típicas de bot.
VIEWPORT = {"width": 1440, "height": 900}
LOCALE = "es-PE"
TIMEZONE = "America/Lima"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

#: `AutomationControlled` es lo que hace que `navigator.webdriver` sea true.
CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
]

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


class DirectScraperProvider(FlightProvider):
    """Base de los scrapers que manejan un navegador de verdad."""

    #: Lo pisa cada subclase.
    airline_name = ""

    def search(self, origin: str, dest: str, date: date) -> list[RawFlightOffer]:
        """Nunca propaga: ante cualquier fallo devuelve `[]` y deja screenshot.

        Normaliza acá, y no en cada scraper, para que la regla "todo precio que
        sale de un provider es final con impuestos" valga por construcción.
        """
        try:
            return self._normalize(self._search(origin, dest, date))
        except Exception:  # noqa: BLE001
            logger.error(
                "%s: búsqueda directa fallida para %s→%s en %s",
                self.source_name, origin, dest, date.isoformat(), exc_info=True,
            )
            return []

    def _search(self, origin: str, dest: str, date: date) -> list[RawFlightOffer]:
        raise NotImplementedError

    def _normalize(self, ofertas: list[RawFlightOffer]) -> list[RawFlightOffer]:
        """Convierte tarifa base a precio final si la fuente publica base."""
        if not self.publishes_base_fare:
            return ofertas

        for oferta in ofertas:
            base = oferta.price_pen
            oferta.price_pen = base_fare_to_final(base)
            logger.debug(
                "%s: tarifa base S/ %s -> precio final S/ %s",
                self.source_name, base, oferta.price_pen,
            )
        return ofertas

    # ------------------------------------------------------------- navegador
    @contextmanager
    def browser_page(self):
        """Página lista para navegar, con el navegador cerrándose siempre."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=CHROMIUM_ARGS)
            context = browser.new_context(
                viewport=VIEWPORT,
                locale=LOCALE,
                timezone_id=TIMEZONE,
                user_agent=USER_AGENT,
            )
            # Refuerzo del anti-detección: los args de Chromium no siempre
            # alcanzan y `navigator.webdriver` es lo primero que miran.
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            page.set_default_timeout(settings.DIRECT_SCRAPER_TIMEOUT_MS)
            try:
                yield page
            finally:
                context.close()
                browser.close()

    def save_failure_screenshot(self, page, origin: str, dest: str, flight_date: date) -> None:
        """Deja evidencia del DOM que rompió el scraper."""
        try:
            carpeta = Path(settings.DIRECT_SCRAPER_SCREENSHOT_DIR)
            carpeta.mkdir(parents=True, exist_ok=True)
            marca = timezone.localtime().strftime("%Y%m%d-%H%M%S")
            destino = carpeta / f"{self.source_name}_{origin}{dest}_{flight_date}_{marca}.png"
            page.screenshot(path=str(destino), full_page=True)
            logger.info("%s: screenshot del fallo en %s", self.source_name, destino)
        except Exception as exc:  # noqa: BLE001 - depurar nunca rompe
            logger.warning("%s: no se pudo guardar el screenshot: %s", self.source_name, exc)


# --------------------------------------------------------------------- helpers
def parse_price_text(raw: str) -> Decimal | None:
    """Precio de un nodo del DOM. `None` si no hay importe."""
    try:
        monto, _moneda = parse_price(raw, default_currency="PEN")
    except PriceParseError:
        return None
    return monto


def parse_time_text(raw: str) -> tuple[int, int] | None:
    """Saca `HH:MM` de un texto que puede traer basura alrededor."""
    match = _TIME_RE.search(raw or "")
    if not match:
        return None
    hora, minuto = int(match.group(1)), int(match.group(2))
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None
    return hora, minuto
