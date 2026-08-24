"""Cálculo de estadísticas de ruta sobre el histórico de snapshots.

Se separa del task de Celery para poder testearlo sin broker ni red.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

CENTS = Decimal("0.01")


@dataclass(frozen=True)
class StatsResult:
    """Resumen de precios de una ruta en la ventana analizada."""

    avg: Decimal | None
    minimum: Decimal | None
    p25: Decimal | None
    median: Decimal | None
    samples: int

    @property
    def is_empty(self) -> bool:
        return self.samples == 0


def percentile(values: Sequence[Decimal], fraction: float) -> Decimal | None:
    """Percentil por interpolación lineal, igual que `numpy.percentile`.

    Args:
        values: precios, en cualquier orden.
        fraction: entre 0 y 1 (0.25 = percentil 25).
    """
    if not values:
        return None
    if not 0 <= fraction <= 1:
        raise ValueError("fraction debe estar entre 0 y 1")

    ordered = sorted(Decimal(v) for v in values)
    if len(ordered) == 1:
        return _round(ordered[0])

    position = Decimal(str(fraction)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower

    return _round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def compute_stats(prices: Sequence[Decimal]) -> StatsResult:
    """Calcula promedio, mínimo, p25 y mediana de una lista de precios."""
    values = [Decimal(p) for p in prices]
    if not values:
        return StatsResult(avg=None, minimum=None, p25=None, median=None, samples=0)

    total = sum(values, Decimal("0"))
    return StatsResult(
        avg=_round(total / len(values)),
        minimum=_round(min(values)),
        p25=percentile(values, 0.25),
        median=percentile(values, 0.50),
        samples=len(values),
    )


def _round(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)
