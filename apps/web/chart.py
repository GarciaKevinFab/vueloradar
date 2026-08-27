"""Gráfico del histórico como SVG generado en el servidor.

Sin librerías de JS: la página tiene que ser liviana y cacheable en el borde
(Cloudflare la sirve estática entre barridos), y en Perú buena parte del
tráfico es móvil con datos limitados.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

WIDTH = 720
HEIGHT = 220
PAD_X = 8
PAD_Y = 16


@dataclass(frozen=True)
class Chart:
    """Geometría lista para inyectar en el `<svg>` de la plantilla."""

    line: str
    area: str
    width: int
    height: int
    min_price: Decimal
    max_price: Decimal
    points: list[tuple[float, float, object]]

    @property
    def is_empty(self) -> bool:
        return not self.line


def build(series) -> Chart:
    """Convierte una serie de `DayPrice` en coordenadas SVG.

    Con menos de dos puntos no hay línea que dibujar y devolvemos un gráfico
    vacío: la plantilla muestra el aviso de histórico insuficiente.
    """
    if len(series) < 2:
        return Chart(line="", area="", width=WIDTH, height=HEIGHT,
                     min_price=Decimal("0"), max_price=Decimal("0"), points=[])

    values = [float(p.price) for p in series]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0

    usable_w = WIDTH - PAD_X * 2
    usable_h = HEIGHT - PAD_Y * 2
    step = usable_w / (len(series) - 1)

    points = []
    for i, item in enumerate(series):
        x = PAD_X + i * step
        # El eje Y se invierte: precio alto va arriba.
        y = PAD_Y + usable_h - ((float(item.price) - lo) / span) * usable_h
        points.append((round(x, 2), round(y, 2), item))

    line = " ".join(f"{x},{y}" for x, y, _ in points)
    area = (
        f"M {points[0][0]},{HEIGHT - PAD_Y} "
        + " ".join(f"L {x},{y}" for x, y, _ in points)
        + f" L {points[-1][0]},{HEIGHT - PAD_Y} Z"
    )

    return Chart(
        line=line, area=area, width=WIDTH, height=HEIGHT,
        min_price=Decimal(str(lo)), max_price=Decimal(str(hi)), points=points,
    )
