"""Tipo de cambio USD→PEN, en vivo.

Las fuentes de precios a veces devuelven dólares; el sistema siempre almacena
soles. La tasa se consulta a APIs públicas y se cachea **una hora**, no un día:
para un producto que mide precios, una tasa de ayer es un dato inventado.

No se pide en cada conversión porque un barrido son ~1.300 consultas, y eso
serían 1.300 llamadas a una API gratuita — la bloquearían con razón. Una hora
es tiempo real a efectos prácticos: el sol se mueve fracciones de porcentaje
intradía.

**No hay tasa fija de respaldo.** Antes existía `FX_FALLBACK_USD_PEN`, un
número del `.env` que se usaba en silencio cuando fallaban las fuentes: eso
guarda un precio inventado en el histórico y nadie se entera. Ahora el respaldo
es la **última tasa buena conocida**, con su antigüedad; si tampoco hay, la
conversión falla y el precio se descarta con aviso al admin. Perder una oferta
es recuperable; contaminar el histórico no.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = "fx:usd_pen"
#: La ultima tasa buena sobrevive mucho mas que el cache normal: es el respaldo.
LAST_GOOD_KEY = "fx:usd_pen:last_good"
_TIMEOUT = 10
_USER_AGENT = "vueloradar/1.0 (+monitor de vuelos domesticos Peru)"

# Fuentes públicas sin API key, en orden de preferencia. Tres y no dos: con dos,
# que ambas estén caídas a la vez deja de ser improbable.
FX_SOURCES: list[tuple[str, tuple[str, ...]]] = [
    ("https://open.er-api.com/v6/latest/USD", ("rates", "PEN")),
    (
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
        ("usd", "pen"),
    ),
    # Frankfurter quedo descartado: solo cubre monedas del BCE y devuelve 404
    # para PEN. Verificado en vivo el 2026-08-27.
    ("https://api.exchangerate-api.com/v4/latest/USD", ("rates", "PEN")),
]

# Rango de cordura: un valor fuera de aquí es un error de la fuente, no una
# devaluación. El sol lleva décadas entre 2.5 y 4.5 por dólar.
MIN_PLAUSIBLE_RATE = Decimal("2.0")
MAX_PLAUSIBLE_RATE = Decimal("6.0")


class RateUnavailable(RuntimeError):
    """No hay ninguna tasa confiable: ni fresca ni última buena vigente."""


def usd_to_pen(*, force_refresh: bool = False) -> Decimal:
    """Cuántos soles vale un dólar ahora.

    Raises:
        RateUnavailable: si ninguna fuente responde y la última tasa buena ya
            superó `FX_LAST_GOOD_MAX_AGE_HOURS`. El caller debe descartar el
            precio, no inventarlo.
    """
    if not force_refresh:
        cached = _decimal_from_cache(CACHE_KEY)
        if cached is not None:
            return cached

    rate = _fetch_rate()
    if rate is not None:
        _cache_set(rate)
        return rate

    ultima = _last_good()
    if ultima is not None:
        tasa, horas = ultima
        logger.warning(
            "fx: todas las fuentes fallaron; se usa la última tasa buena "
            "USD/PEN=%s de hace %.1f h",
            tasa,
            horas,
        )
        return tasa

    _avisar_al_admin()
    raise RateUnavailable(
        "sin tipo de cambio USD/PEN: ninguna fuente respondió y no hay tasa "
        "reciente guardada"
    )


def convert_to_pen(amount: Decimal, currency: str) -> Decimal | None:
    """Convierte a soles. Devuelve None si el precio no se puede convertir.

    None significa "descarta esta oferta", no "usa el número igual".
    """
    code = (currency or "PEN").upper()
    if code == "PEN":
        return Decimal(amount).quantize(Decimal("0.01"))

    if code != "USD":
        logger.warning("fx: moneda no soportada %s; la oferta se descarta", code)
        return None

    try:
        return (Decimal(amount) * usd_to_pen()).quantize(Decimal("0.01"))
    except RateUnavailable as exc:
        logger.error("fx: %s; la oferta en USD se descarta", exc)
        return None


def _decimal_from_cache(key: str) -> Decimal | None:
    """Lee del cache. Si Redis está caído, se sigue sin cache."""
    try:
        raw = cache.get(key)
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
    """Guarda la tasa fresca y, aparte, como última buena conocida."""
    try:
        cache.set(CACHE_KEY, str(rate), settings.FX_CACHE_TTL_SECONDS)
        cache.set(
            LAST_GOOD_KEY,
            json.dumps({"rate": str(rate), "at": timezone.now().isoformat()}),
            settings.FX_LAST_GOOD_MAX_AGE_HOURS * 3600,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fx: cache inaccesible al escribir: %s", exc)
        return
    logger.info(
        "fx: USD/PEN=%s cacheado por %d min",
        rate,
        settings.FX_CACHE_TTL_SECONDS // 60,
    )


def _last_good() -> tuple[Decimal, float] | None:
    """Última tasa buena y su antigüedad en horas, si sigue vigente."""
    try:
        raw = cache.get(LAST_GOOD_KEY)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None

    try:
        datos = json.loads(raw)
        tasa = Decimal(str(datos["rate"]))
        momento = timezone.datetime.fromisoformat(datos["at"])
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return None

    horas = (timezone.now() - momento).total_seconds() / 3600
    if horas > settings.FX_LAST_GOOD_MAX_AGE_HOURS:
        return None
    return tasa, horas


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


def _avisar_al_admin() -> None:
    """Quedarse sin tipo de cambio es un incidente, no una nota en el log."""
    try:
        from .notify import send_admin_alert

        send_admin_alert(
            "VueloRadar: sin tipo de cambio USD/PEN. Ninguna fuente respondió y "
            "no hay tasa reciente guardada; las ofertas en dólares se están "
            "descartando."
        )
    except Exception as exc:  # noqa: BLE001 - avisar no puede romper el barrido
        logger.warning("fx: no se pudo avisar al admin: %s", exc)
