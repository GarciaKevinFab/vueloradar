"""Gráficos del histórico, generados en el servidor como SVG.

Sin librerías de JS: la página tiene que ser liviana y cacheable en el borde, y
en Perú buena parte del tráfico es móvil con datos limitados.

Dos piezas:

- `build` — el gráfico grande de la ficha. Además de la curva dibuja las
  **bandas de referencia** contra las que se juzga el precio. Sin ellas el
  gráfico es decorativo: se ve que el precio subió o bajó, pero no *respecto de
  qué*, que es justamente lo que el veredicto afirma.
- `sparkline` — la miniatura de la portada. Sale gratis: la serie ya está
  cargada para calcular la tendencia.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

WIDTH = 720
HEIGHT = 220
PAD_X = 8
PAD_Y = 16

SPARK_W = 132
SPARK_H = 30
SPARK_PAD = 3


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
    #: Largo del trazo en px. La animacion de dibujado necesita el valor exacto
    #: para `stroke-dasharray`; calcularlo aca evita tener que medirlo con JS.
    length: float = 0.0
    #: Coordenadas Y de las referencias, o None si caen fuera del rango dibujado.
    p25_y: float | None = None
    median_y: float | None = None
    #: El punto mas barato de la serie, para marcarlo.
    min_point: tuple[float, float] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.line

    @property
    def tiene_referencias(self) -> bool:
        return self.p25_y is not None or self.median_y is not None


@dataclass(frozen=True)
class Sparkline:
    """Miniatura de tendencia para la lista de rutas."""

    line: str
    width: int = SPARK_W
    height: int = SPARK_H
    #: True si el ultimo valor es el mas bajo de la serie.
    en_minimo: bool = False
    ultimo: tuple[float, float] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.line


def _escala(valores, alto: int, pad: int):
    """Devuelve una función precio → coordenada Y, más el rango de la serie."""
    lo, hi = min(valores), max(valores)
    span = (hi - lo) or 1.0
    usable = alto - pad * 2

    def y(valor: float) -> float:
        # El eje Y se invierte: precio alto va arriba.
        return round(pad + usable - ((valor - lo) / span) * usable, 2)

    return y, lo, hi


def build(series, stats=None) -> Chart:
    """Convierte una serie de `DayPrice` en coordenadas SVG.

    Args:
        series: puntos con `.day` y `.price`.
        stats: `RouteStats` opcional. Si viene, se dibujan el p25 y la mediana
            como referencias: son los umbrales que usa el veredicto.

    Con menos de dos puntos no hay línea que dibujar y devolvemos un gráfico
    vacío: la plantilla muestra el aviso de histórico insuficiente.
    """
    if len(series) < 2:
        return Chart(line="", area="", width=WIDTH, height=HEIGHT,
                     min_price=Decimal("0"), max_price=Decimal("0"), points=[], length=0.0)

    valores = [float(p.price) for p in series]

    # El rango se estira para que las referencias entren en el dibujo. Sin esto,
    # una semana entera por debajo del p25 dejaba el grafico sin referencias
    # justo cuando lo que hay que mostrar es que estuvo barata todo el tiempo.
    referencias = [
        float(v) for v in (getattr(stats, "p25_30d", None), getattr(stats, "median_30d", None))
        if v is not None
    ]
    minimo, maximo = min(valores), max(valores)
    margen = (maximo - minimo) or maximo * 0.1 or 1.0
    # Se ignora una referencia absurdamente lejana: aplastaria la curva.
    utiles = [r for r in referencias if minimo - margen * 2 <= r <= maximo + margen * 2]
    y_de, lo, hi = _escala(valores + utiles, HEIGHT, PAD_Y)

    usable_w = WIDTH - PAD_X * 2
    step = usable_w / (len(series) - 1)

    points = [
        (round(PAD_X + i * step, 2), y_de(float(item.price)), item)
        for i, item in enumerate(series)
    ]

    line = " ".join(f"{x},{y}" for x, y, _ in points)
    area = (
        f"M {points[0][0]},{HEIGHT - PAD_Y} "
        + " ".join(f"L {x},{y}" for x, y, _ in points)
        + f" L {points[-1][0]},{HEIGHT - PAD_Y} Z"
    )
    largo = sum(
        math.dist(points[i][:2], points[i + 1][:2]) for i in range(len(points) - 1)
    )

    def dentro(valor) -> float | None:
        """Y de una referencia, solo si entró en el rango dibujado."""
        if valor is None:
            return None
        numero = float(valor)
        return y_de(numero) if lo <= numero <= hi else None

    mas_barato = min(points, key=lambda p: float(p[2].price))

    return Chart(
        line=line, area=area, width=WIDTH, height=HEIGHT,
        min_price=Decimal(str(minimo)), max_price=Decimal(str(maximo)), points=points,
        length=round(largo, 1),
        p25_y=dentro(getattr(stats, "p25_30d", None)),
        median_y=dentro(getattr(stats, "median_30d", None)),
        min_point=(mas_barato[0], mas_barato[1]),
    )


def sparkline(precios) -> Sparkline:
    """Miniatura de la tendencia de una ruta.

    La serie ya viene cargada para el veredicto de tendencia, así que esto no
    cuesta una consulta más: es información que estábamos tirando.
    """
    valores = [float(p) for p in precios]
    if len(valores) < 2:
        return Sparkline(line="")

    y_de, lo, _ = _escala(valores, SPARK_H, SPARK_PAD)
    step = (SPARK_W - SPARK_PAD * 2) / (len(valores) - 1)
    puntos = [(round(SPARK_PAD + i * step, 2), y_de(v)) for i, v in enumerate(valores)]

    return Sparkline(
        line=" ".join(f"{x},{y}" for x, y in puntos),
        en_minimo=valores[-1] <= lo,
        ultimo=puntos[-1],
    )
