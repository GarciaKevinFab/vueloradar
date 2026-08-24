"""Tipo de cambio USD→PEN.

Las fuentes de precios a veces devuelven dólares; el sistema siempre almacena
soles. Se consulta una API pública gratuita (sin API key) y se cachea 24h en
Redis. Si todo falla, se usa `FX_FALLBACK_USD_PEN` del `.env`.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "fx:usd_pen"
_TIMEOUT = 10
_USER_AGENT = "vueloradar/1.0 (+monitor de vuelos domesticos Peru)"

# Fuentes públicas sin API key, en orden de preferencia.
# Cada entrada es (url, ruta de claves hasta la tasa PEN).
FX_SOURCES: list[tuple[str, tuple[str, ...]]] = [
    ("https://open.er-api.com/v6/latest/USD", ("rates", "PEN")),
    (
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
        ("usd", "pen"),
    ),
]

# Rango de cordura: un valor fuera de aquí es un error de la fuente, no una
# devaluación. El sol lleva décadas entre 2.5 y 4.5 por dólar.
MIN_PLAUSIBLE_RATE = Decimal("2.0")
MAX_PLAUSIBLE_RATE = Decimal("6.0")


def usd_to_pen(*, force_refresh: bool = False) -> Decimal:
    """Devuelve cuántos soles vale un dólar hoy.

    Nunca lanza excepción: ante cualquier fallo devuelve el fallback del `.env`.
    """
    if not force_refresh:
        cached = _cache_get()
        if cached is not None:
            return cached

    rate = _fetch_rate()
    if rate is None:
        fallback = _fallback_rate()
        logger.warning("fx: usando fallback USD/PEN=%s (todas las fuentes fallaron)", fallback)
        return fallback

    _cache_set(rate)
    return rate


def _cache_get() -> Decimal | None:
    """Lee del cache. Si Redis está caído, se sigue sin cache."""
    try:
        raw = cache.get(CACHE_KEY)
    except Exception as exc:  # noqa: BLE001 - Redis caído no puede romper una búsqueda
        logger.warning("fx: cache inaccesible al leer: %s", exc)
        return None

    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _cache_set(rate: Decimal) -> None:
    try:
        cache.set(CACHE_KEY, str(rate), settings.FX_CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fx: cache inaccesible al escribir: %s", exc)
        return
    logger.info("fx: tipo de cambio USD/PEN=%s cacheado por 24h", rate)


def convert_to_pen(amount: Decimal, currency: str) -> Decimal:
    """Convierte `amount` en `currency` a soles, redondeado a 2 decimales."""
    code = (currency or "PEN").upper()
    if code == "PEN":
        return Decimal(amount).quantize(Decimal("0.01"))
    if code == "USD":
        return (Decimal(amount) * usd_to_pen()).quantize(Decimal("0.01"))

    logger.warning("fx: moneda no soportada %s, se asume PEN", code)
    return Decimal(amount).quantize(Decimal("0.01"))


def _fetch_rate() -> Decimal | None:
    for url, path in FX_SOURCES:
        try:
            payload = _get_json(url)
            raw = payload
            for key in path:
                raw = raw[key]
            rate = Decimal(str(raw)).quantize(Decimal("0.0001"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("fx: fuente %s inalcanzable: %s", url, exc)
            continue
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            logger.warning("fx: respuesta inesperada de %s: %s", url, exc)
            continue

        if MIN_PLAUSIBLE_RATE <= rate <= MAX_PLAUSIBLE_RATE:
            return rate
        logger.warning("fx: tasa fuera de rango desde %s: %s", url, rate)

    return None


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _fallback_rate() -> Decimal:
    try:
        return Decimal(str(settings.FX_FALLBACK_USD_PEN)).quantize(Decimal("0.0001"))
    except (InvalidOperation, TypeError):  # pragma: no cover - config rota
        return Decimal("3.8000")
