"""Precio de venta: costo del pasaje más el margen del operador.

El sistema no solo informa precios, también sirve para revender. Esta capa
convierte el costo real en lo que se le cotiza al cliente.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from django.conf import settings

CENTS = Decimal("0.01")


@dataclass(frozen=True)
class SalePrice:
    """Descomposición de una cotización."""

    cost: Decimal        # lo que cuesta el pasaje
    margin: Decimal      # la ganancia del operador
    sale: Decimal        # lo que paga el cliente

    @property
    def margin_pct(self) -> Decimal:
        if self.cost <= 0:
            return Decimal("0")
        return ((self.margin / self.cost) * 100).quantize(Decimal("0.1"))


def quote(cost) -> SalePrice:
    """Calcula el precio de venta a partir del costo.

    El margen es el mayor entre el porcentaje configurado y un piso fijo: un
    10% sobre un pasaje barato no cubre el trabajo de gestionar la compra.
    """
    costo = Decimal(cost).quantize(CENTS)
    if costo <= 0:
        return SalePrice(cost=costo, margin=Decimal("0.00"), sale=costo)

    por_porcentaje = costo * settings.SALE_MARKUP_PCT / 100
    margen = max(por_porcentaje, settings.SALE_MARKUP_MIN_PEN)

    venta = _round_up(costo + margen)
    # El margen real es el que queda tras redondear, no el teórico.
    return SalePrice(cost=costo, margin=(venta - costo).quantize(CENTS), sale=venta)


def _round_up(value: Decimal) -> Decimal:
    """Redondea hacia arriba al múltiplo configurado. Cotizar S/ 487,33 se ve mal."""
    paso = settings.SALE_ROUND_TO_PEN
    if paso <= 0:
        return value.quantize(CENTS, rounding=ROUND_HALF_UP)
    return (value / paso).quantize(Decimal("1"), rounding=ROUND_CEILING) * paso
