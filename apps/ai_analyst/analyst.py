"""Veredicto de compra: "comprá ahora" o "esperá", con la razón.

El contexto que se le manda al modelo sale entero de la base — stats de 30
días y la evolución del precio para esa ruta y fecha. El modelo no inventa
números, solo los interpreta.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .llm_router import complete_json
from .prompts import ANALYST_CONTEXT, ANALYST_SYSTEM

logger = logging.getLogger(__name__)

BUY = "comprar"
WAIT = "esperar"

HISTORY_SNAPSHOTS = 15
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@dataclass(frozen=True)
class Verdict:
    """Lo que opina el analista sobre este precio."""

    action: str          # "comprar" | "esperar"
    confidence: int      # 0-100
    reason: str
    provider: str = ""

    @property
    def is_buy(self) -> bool:
        return self.action == BUY

    @property
    def label(self) -> str:
        return "COMPRA" if self.is_buy else "ESPERA"


def get_verdict(route, flight_date: date, current_price) -> Verdict | None:
    """Veredicto para un precio concreto, o `None` si no se puede opinar.

    Devuelve `None` cuando no hay histórico suficiente o cuando ningún
    proveedor de IA respondió. El caller debe seguir funcionando sin veredicto.
    """
    stats = _get_stats(route)
    if stats is None or stats.samples_count < settings.VERDICT_MIN_SAMPLES:
        logger.info("analyst: sin histórico suficiente para %s", route)
        return None

    clave = _cache_key(route, flight_date, current_price)
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado

    contexto = build_context(route, flight_date, current_price, stats)

    resultado = complete_json(ANALYST_SYSTEM, contexto, max_tokens=400)
    if resultado is None:
        logger.warning("analyst: sin veredicto, ningún proveedor respondió")
        return None

    crudo, proveedor = resultado
    verdict = _normalize(crudo, proveedor)
    if verdict is None:
        return None

    _cache_set(clave, verdict)
    return verdict


def build_context(route, flight_date: date, current_price, stats) -> str:
    """Arma el texto que ve el modelo. Todo sale de la base."""
    from apps.flights.models import PriceSnapshot

    historial = list(
        PriceSnapshot.objects.filter(route=route, flight_date=flight_date)
        .order_by("-snapshot_at")
        .values_list("snapshot_at", "min_price_pen")[:HISTORY_SNAPSHOTS]
    )
    historial.reverse()

    if historial:
        lineas = "\n".join(
            f"- {timezone.localtime(cuando):%d/%m %H:%M}: S/ {precio}"
            for cuando, precio in historial
        )
    else:
        lineas = "- (sin snapshots previos para esta fecha de vuelo)"

    faltan = (flight_date - timezone.localdate()).days

    return ANALYST_CONTEXT.format(
        origin=route.origin_id,
        destination=route.destination_id,
        origin_city=route.origin.city,
        destination_city=route.destination.city,
        flight_date=flight_date.isoformat(),
        weekday=DIAS[flight_date.weekday()],
        days_ahead=faltan,
        current_price=current_price,
        avg_30d=stats.avg_30d,
        median_30d=stats.median_30d,
        p25_30d=stats.p25_30d,
        min_30d=stats.min_30d,
        samples_count=stats.samples_count,
        history=lineas,
    )


# --------------------------------------------------------------- normalización
def _normalize(crudo: dict, proveedor: str) -> Verdict | None:
    """Valida lo que devolvió el modelo. Un veredicto sin razón no sirve."""
    accion = str(crudo.get("action", "")).strip().lower()
    if accion not in (BUY, WAIT):
        # Algunos modelos de respaldo responden "buy"/"wait" en inglés.
        accion = {"buy": BUY, "wait": WAIT}.get(accion, "")
    if not accion:
        logger.warning("analyst: acción irreconocible %r", crudo.get("action"))
        return None

    razon = str(crudo.get("reason", "")).strip()
    if not razon:
        logger.warning("analyst: veredicto sin razón, se descarta")
        return None

    try:
        confianza = int(float(crudo.get("confidence", 0)))
    except (TypeError, ValueError):
        confianza = 0
    confianza = max(0, min(confianza, 100))

    return Verdict(action=accion, confidence=confianza, reason=razon[:400], provider=proveedor)


# --------------------------------------------------------------------- cache
def _get_stats(route):
    from apps.flights.models import RouteStats

    return RouteStats.objects.filter(route=route).first()


def _cache_key(route, flight_date: date, current_price) -> str:
    """Se cachea por banda de precio: 202 y 205 comparten veredicto."""
    try:
        banda = (Decimal(current_price) // settings.VERDICT_PRICE_BAND) * settings.VERDICT_PRICE_BAND
    except (InvalidOperation, TypeError):
        banda = Decimal("0")
    crudo = f"{route.pk}:{flight_date.isoformat()}:{banda}"
    return "verdict:" + hashlib.sha256(crudo.encode()).hexdigest()[:32]


def _cache_get(clave: str) -> Verdict | None:
    try:
        datos = cache.get(clave)
    except Exception:  # noqa: BLE001
        return None
    if not datos:
        return None
    return Verdict(**datos)


def _cache_set(clave: str, verdict: Verdict) -> None:
    try:
        cache.set(
            clave,
            {
                "action": verdict.action,
                "confidence": verdict.confidence,
                "reason": verdict.reason,
                "provider": verdict.provider,
            },
            settings.VERDICT_CACHE_TTL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyst: no se pudo cachear el veredicto: %s", exc)
