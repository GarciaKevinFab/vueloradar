"""Registro de proveedores activos.

Google Flights sostiene el barrido masivo. Los scrapers directos son pesados
(un Chromium por búsqueda) y frágiles, así que quedan detrás de flags y **no**
se usan en el barrido: sirven para verificar un precio antes de alertar y para
rutas marcadas con `use_direct_scrapers`.
"""

from __future__ import annotations

from django.conf import settings

from .base import FlightProvider
from .google_flights import GoogleFlightsProvider


def get_active_providers() -> list[FlightProvider]:
    """Proveedores del barrido masivo, en orden de preferencia."""
    return [GoogleFlightsProvider()]


def get_direct_providers() -> list[FlightProvider]:
    """Scrapers directos habilitados por settings. Puede devolver lista vacía."""
    proveedores: list[FlightProvider] = []

    if settings.ENABLE_SKY_SCRAPER:
        from .sky import SkyProvider

        proveedores.append(SkyProvider())

    if settings.ENABLE_JETSMART_SCRAPER:
        from .jetsmart import JetSmartProvider

        proveedores.append(JetSmartProvider())

    return proveedores


def get_providers_for_route(route=None) -> list[FlightProvider]:
    """Proveedores a usar en una ruta concreta.

    Solo las rutas marcadas explícitamente pagan el costo de los scrapers
    directos; el resto va con Google Flights nomás. Sin ruta (tramos sueltos
    de una conexión) se usa la lista base.
    """
    proveedores = get_active_providers()
    if route is not None and getattr(route, "use_direct_scrapers", False):
        proveedores.extend(get_direct_providers())
    return proveedores
