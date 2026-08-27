"""Veredicto público: ¿el precio de hoy es bueno para esta ruta?

Es la pieza que diferencia al sitio de un metabuscador: decimos "esperá"
cuando conviene esperar. La lógica es pura (sin ORM) para poder testearla
sin base de datos, igual que `apps/flights/stats.py`.

Los umbrales se derivan de los mismos ajustes que usa el motor de alertas
(`DEAL_P25_FACTOR`, `VERDICT_MIN_SAMPLES`) para que el bot y la web nunca
digan cosas distintas sobre el mismo precio.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from apps.flights.stats import compute_stats

#: Por encima de la mediana en este factor, el precio se considera caro.
EXPENSIVE_MEDIAN_FACTOR = Decimal("1.20")

#: Días mínimos de serie para opinar sobre la tendencia de la ruta.
MIN_TREND_DAYS = 14

CHOLLO = "chollo"
BUENO = "bueno"
NORMAL = "normal"
ALTO = "alto"
CARO = "caro"
SIN_DATOS = "sin_datos"

#: Etiqueta, acción recomendada y color por veredicto.
LABELS = {
    CHOLLO: ("Chollo", "Comprá ahora", "#0f9d58"),
    BUENO: ("Buen precio", "Buen momento para comprar", "#2e7d32"),
    NORMAL: ("Precio normal", "Podés comprar sin apuro", "#8a6d1f"),
    ALTO: ("Precio alto", "Conviene esperar", "#d97706"),
    CARO: ("Caro", "Esperá, esto no es precio", "#c5221f"),
    SIN_DATOS: ("Sin histórico suficiente", "Todavía no opinamos", "#5f6368"),
}


@dataclass(frozen=True)
class Verdict:
    """Resultado de evaluar un precio contra el histórico de su ruta."""

    level: str
    label: str
    action: str
    color: str
    #: Diferencia porcentual contra la mediana de 30 días. Negativo = más barato.
    vs_median_pct: int | None
    samples: int
    #: Días de serie que faltan para poder opinar sobre la tendencia. Solo lo
    #: llena `evaluate_trend`; en el veredicto por fecha es None.
    missing_days: int | None = None

    @property
    def is_actionable(self) -> bool:
        """¿Tenemos histórico suficiente para que el veredicto valga algo?"""
        return self.level != SIN_DATOS

    @property
    def should_buy(self) -> bool:
        return self.level in (CHOLLO, BUENO)


@dataclass(frozen=True)
class _SeriesStats:
    """Adaptador con la forma de `RouteStats` para series calculadas al vuelo.

    Permite reusar los mismos umbrales sin duplicar la lógica de corte.
    """

    p25_30d: Decimal | None
    median_30d: Decimal | None
    avg_30d: Decimal | None
    samples_count: int


def evaluate(price: Decimal | None, stats) -> Verdict:
    """Clasifica el precio **de una fecha concreta** contra la distribución
    de precios de la ruta en 30 días.

    La comparación es válida porque el precio de una fecha es una muestra de
    esa misma distribución. NO sirve para juzgar el mínimo entre muchas fechas
    (siempre caería en el percentil bajo por construcción); para eso está
    `evaluate_trend`.

    Args:
        price: precio en soles de una fecha de vuelo, o None si no hay dato.
        stats: instancia de `RouteStats` (o None si la ruta no tiene todavía).

    Sin muestras suficientes devolvemos `SIN_DATOS` en vez de inventar un
    veredicto: un p25 sobre cuatro observaciones no significa nada.
    """
    if price is None or stats is None or stats.p25_30d is None or stats.median_30d is None:
        return _build(SIN_DATOS, None, getattr(stats, "samples_count", 0))

    if stats.samples_count < settings.VERDICT_MIN_SAMPLES:
        return _build(SIN_DATOS, None, stats.samples_count)

    price = Decimal(price)
    p25 = Decimal(stats.p25_30d)
    median = Decimal(stats.median_30d)

    vs_median = int(((price - median) / median * 100).to_integral_value()) if median else None

    if price <= p25 * settings.DEAL_P25_FACTOR:
        level = CHOLLO
    elif price <= p25 and price < median:
        # `price < median` evita el caso degenerado de una serie plana, donde
        # p25 == mediana y el precio de siempre se leería como oferta.
        level = BUENO
    elif price <= median:
        level = NORMAL
    elif price <= median * EXPENSIVE_MEDIAN_FACTOR:
        level = ALTO
    else:
        level = CARO

    return _build(level, vs_median, stats.samples_count)


def _build(level: str, vs_median_pct: int | None, samples: int,
           missing_days: int | None = None) -> Verdict:
    label, action, color = LABELS[level]
    return Verdict(
        level=level, label=label, action=action, color=color,
        vs_median_pct=vs_median_pct, samples=samples, missing_days=missing_days,
    )


def evaluate_trend(today_min: Decimal | None, daily_minimums) -> Verdict:
    """¿La ruta está más barata hoy que de costumbre?

    Compara el mínimo de hoy contra el histórico de **mínimos diarios**, que es
    la única serie comparable con él. Con pocos días de histórico no opinamos:
    dos semanas es el piso para que un percentil signifique algo.

    Args:
        today_min: precio mínimo vigente de la ruta, sobre cualquier fecha.
        daily_minimums: precios mínimos observados un día por punto.
    """
    valores = [Decimal(p) for p in daily_minimums]
    if today_min is None or len(valores) < MIN_TREND_DAYS:
        # Decir cuántos días faltan convierte un "no sé" en una promesa
        # verificable: el barrido suma un punto por día, solo.
        return _build(
            SIN_DATOS, None, len(valores),
            missing_days=max(0, MIN_TREND_DAYS - len(valores)),
        )

    resumen = compute_stats(valores)
    return evaluate(
        today_min,
        _SeriesStats(
            p25_30d=resumen.p25,
            median_30d=resumen.median,
            avg_30d=resumen.avg,
            samples_count=resumen.samples,
        ),
    )
