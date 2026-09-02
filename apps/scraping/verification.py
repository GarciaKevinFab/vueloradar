"""Verificación del precio contra la web de la aerolínea.

Google Flights a veces muestra tarifas que no existen en el checkout real
(promos de app, equipaje incluido de otra forma). Antes de decirle a alguien
"compra ya", vale la pena confirmar con la fuente.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings

from .providers.registry import get_direct_providers

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationResult:
    """Qué dijo la aerolínea sobre el precio que traíamos."""

    price: Decimal          # el precio a usar de acá en adelante
    verified: bool          # si algún scraper directo respondió
    source: str             # de dónde salió `price`
    original_price: Decimal
    discrepancy_pct: Decimal = Decimal("0")

    @property
    def is_significant(self) -> bool:
        return abs(self.discrepancy_pct) >= settings.ALERT_PRICE_DISCREPANCY_PCT


def verify_price(origin: str, dest: str, flight_date: date, price) -> VerificationResult:
    """Contrasta `price` con lo que cotiza la aerolínea.

    Si ningún scraper directo está habilitado o todos fallan, devuelve el
    precio original marcado como no verificado — nunca bloquea la alerta.
    """
    original = Decimal(price)
    sin_verificar = VerificationResult(
        price=original, verified=False, source="google_flights", original_price=original
    )

    proveedores = get_direct_providers()
    if not proveedores:
        return sin_verificar

    for provider in proveedores:
        ofertas = provider.search(origin, dest, flight_date)
        if not ofertas:
            continue

        directo = min(o.price_pen for o in ofertas)
        diferencia = (directo - original) / original * 100 if original > 0 else Decimal("0")
        diferencia = diferencia.quantize(Decimal("0.1"))

        if abs(diferencia) >= settings.ALERT_PRICE_DISCREPANCY_PCT:
            logger.warning(
                "verification: discrepancia de %.1f%% en %s→%s %s "
                "(Google S/ %s vs %s S/ %s). Gana el directo.",
                diferencia, origin, dest, flight_date, original, provider.source_name, directo,
            )
            return VerificationResult(
                price=directo, verified=True, source=provider.source_name,
                original_price=original, discrepancy_pct=diferencia,
            )

        logger.info(
            "verification: %s confirma el precio de %s→%s %s (diferencia %.1f%%)",
            provider.source_name, origin, dest, flight_date, diferencia,
        )
        return VerificationResult(
            price=original, verified=True, source=provider.source_name,
            original_price=original, discrepancy_pct=diferencia,
        )

    logger.info("verification: ningún scraper directo respondió para %s→%s", origin, dest)
    return sin_verificar
