"""Lo que el histórico sabe y una tarifa suelta no puede decir.

Tres preguntas que un comparador no puede responder mirando el precio de hoy,
porque exigen haber estado midiendo:

1. ¿Qué día de la semana conviene volar?
2. ¿Cuántos días antes conviene comprar?
3. ¿Qué aerolínea gana de verdad en esta ruta?

Todo sale de `PriceSnapshot`, con lecturas agregadas: una consulta por
pregunta, sin recorrer filas en Python.

**Nada opina sin muestras suficientes.** Cada función devuelve `None` cuando el
histórico no alcanza, igual que el veredicto de compra: decir «los miércoles
son más baratos» con doce observaciones sería inventar, y el activo del sitio
es que cuando afirma algo, lo respalda.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count
from django.db.models.functions import ExtractWeekDay
from django.utils import timezone

from apps.flights.models import PriceSnapshot

#: Ventana de histórico que se mira. Más atrás el mercado ya era otro.
DIAS_DE_HISTORIA = 90

#: Mínimos para abrir la boca. El de aerolínea es más bajo porque la pregunta
#: («cuál aparece más barata») tolera más ruido que una diferencia de precio.
MIN_MUESTRAS_TOTAL = 300
MIN_MUESTRAS_POR_GRUPO = 20
MIN_MUESTRAS_AEROLINEA = 40

#: `ExtractWeekDay` numera 1=domingo … 7=sábado en todos los backends que usa
#: Django. Se traduce acá y no en la plantilla para que el orden de la semana
#: peruana (lunes primero) sea una decisión del dominio, no del HTML.
#: Nombre largo para la frase, corto para el pie de la barra. En un teléfono
#: de 375 px caben siete columnas de 36 px: "miércoles" no entra y truncarlo
#: con puntos suspensivos borra justo el dato que la barra viene a mostrar.
#: El nombre largo va en PLURAL porque los cuatro lugares donde aparece hablan
#: del día en genérico ("los sábados salen más baratos"), nunca de una fecha
#: concreta. De lunes a viernes el plural es idéntico al singular y el defecto
#: pasaba inadvertido; sábado y domingo salían publicados como "de los sábado".
_DIAS = {2: ("lunes", "lun"), 3: ("martes", "mar"), 4: ("miércoles", "mié"),
         5: ("jueves", "jue"), 6: ("viernes", "vie"), 7: ("sábados", "sáb"),
         1: ("domingos", "dom")}
_ORDEN_SEMANA = [2, 3, 4, 5, 6, 7, 1]

#: Franjas de anticipación. Los cortes no son arbitrarios: 0-6 es el pánico de
#: última hora, 7-14 la ventana donde las aerolíneas todavía ajustan por
#: ocupación, y de ahí para arriba el precio lo fija el calendario más que la
#: demanda observada.
FRANJAS = [
    (0, 6, "menos de una semana", "<1 sem"),
    (7, 14, "1 a 2 semanas", "1–2 sem"),
    (15, 30, "2 a 4 semanas", "2–4 sem"),
    (31, 60, "1 a 2 meses", "1–2 mes"),
    (61, 400, "más de 2 meses", "2+ mes"),
]


@dataclass(frozen=True)
class Barra:
    """Un grupo comparable: etiqueta, precio y su peso relativo.

    `alto_pct` es lo que la plantilla dibuja. Se calcula acá porque depende del
    conjunto entero (el máximo del grupo), y una plantilla no puede mirar hacia
    adelante dentro de un bucle.
    """

    etiqueta: str
    #: Versión corta, para el pie de la barra en pantallas angostas.
    corto: str
    precio: Decimal
    muestras: int
    alto_pct: int
    es_mejor: bool


@dataclass(frozen=True)
class Hallazgo:
    """Un análisis completo: las barras y sus extremos."""

    barras: list[Barra]
    mejor: Barra
    peor: Barra

    @property
    def ahorro_pct(self) -> int:
        """Cuánto se ahorra eligiendo bien, en porcentaje sobre lo más caro."""
        if not self.peor.precio:
            return 0
        return int(round((self.peor.precio - self.mejor.precio) / self.peor.precio * 100))

    @property
    def ahorro_pen(self) -> Decimal:
        return (self.peor.precio - self.mejor.precio).quantize(Decimal("1"))

    @property
    def vale_la_pena(self) -> bool:
        """Por debajo del 5% la diferencia es ruido y no merece un titular."""
        return self.ahorro_pct >= 5


@dataclass(frozen=True)
class Aerolinea:
    nombre: str
    veces: int
    cuota_pct: int


def _base(route=None):
    """Snapshots de la ventana de análisis, opcionalmente de una ruta."""
    desde = timezone.localdate() - timedelta(days=DIAS_DE_HISTORIA)
    qs = PriceSnapshot.objects.filter(snapshot_at__date__gte=desde)
    return qs.filter(route=route) if route is not None else qs


def _armar(grupos: list[tuple[str, str, Decimal, int]]) -> Hallazgo | None:
    """Convierte (etiqueta, corto, precio, muestras) en barras comparables."""
    grupos = [g for g in grupos if g[3] >= MIN_MUESTRAS_POR_GRUPO]
    if len(grupos) < 3:
        # Con menos de tres grupos no hay comparación posible: dos puntos son
        # una anécdota, no una distribución.
        return None
    if sum(g[3] for g in grupos) < MIN_MUESTRAS_TOTAL:
        return None

    precios = [g[2] for g in grupos]
    barato, caro = min(precios), max(precios)
    rango = caro - barato

    barras = []
    for etiqueta, corto, precio, muestras in grupos:
        # El alto se mide sobre el RANGO, no sobre el precio absoluto: entre
        # S/ 313 y S/ 365 hay un 14%, y barras al 86% y al 100% no dejarían ver
        # nada. Se reserva un 22% de piso para que la más barata siga visible.
        proporcion = 0.0 if not rango else float((precio - barato) / rango)
        barras.append(Barra(
            etiqueta=etiqueta,
            corto=corto,
            precio=precio.quantize(Decimal("1")),
            muestras=muestras,
            alto_pct=int(round(22 + proporcion * 78)),
            es_mejor=precio == barato,
        ))

    mejor = next(b for b in barras if b.es_mejor)
    peor = max(barras, key=lambda b: b.precio)
    return Hallazgo(barras=barras, mejor=mejor, peor=peor)


def weekday_prices(route=None) -> Hallazgo | None:
    """¿Qué día de la semana sale más barato volar?"""
    filas = {
        r["d"]: r
        for r in _base(route)
        .annotate(d=ExtractWeekDay("flight_date"))
        .values("d")
        .annotate(precio=Avg("min_price_pen"), n=Count("id"))
    }
    return _armar([
        (_DIAS[d][0], _DIAS[d][1], filas[d]["precio"], filas[d]["n"])
        for d in _ORDEN_SEMANA
        if d in filas and filas[d]["precio"] is not None
    ])


def booking_windows(route=None) -> Hallazgo | None:
    """¿Cuántos días antes del vuelo conviene comprar?

    Una sola consulta trae el promedio por día de anticipación y las franjas se
    arman en Python: son ~60 filas, y un `CASE` en SQL escondería los cortes de
    `FRANJAS` dentro de una expresión imposible de leer.
    """
    por_dia = {
        r["days_ahead"]: (r["precio"], r["n"])
        for r in _base(route)
        .filter(days_ahead__isnull=False)
        .values("days_ahead")
        .annotate(precio=Avg("min_price_pen"), n=Count("id"))
    }

    grupos = []
    for desde, hasta, etiqueta, corto in FRANJAS:
        dias = [d for d in por_dia if desde <= d <= hasta]
        muestras = sum(por_dia[d][1] for d in dias)
        if not muestras:
            continue
        # Promedio ponderado por muestras: un día con 400 observaciones no
        # puede pesar lo mismo que uno con 3.
        total = sum(por_dia[d][0] * por_dia[d][1] for d in dias)
        grupos.append((etiqueta, corto, total / muestras, muestras))

    return _armar(grupos)


#: Semanas de anticipación, para el detalle de `/cuando-comprar/`. Las franjas
#: de `FRANJAS` sirven para dar un titular; acá hace falta el grano fino,
#: porque lo interesante es la FORMA de la curva y no el mínimo suelto.
#: Se corta en 62 días porque el barrido no mira más lejos: afirmar algo sobre
#: los 70 días sería inventar sobre datos que no tenemos.
SEMANAS = [(i * 7, i * 7 + 6, f"{i * 7}–{i * 7 + 6} días", str(i * 7)) for i in range(9)]


def booking_curve(route=None) -> Hallazgo | None:
    """El precio semana a semana antes del vuelo.

    Es la misma pregunta que `booking_windows`, con más resolución: sirve para
    mostrar que la curva tiene forma de U —caro a último momento y caro
    demasiado temprano— y no para dar un titular.
    """
    por_dia = {
        r["days_ahead"]: (r["precio"], r["n"])
        for r in _base(route)
        .filter(days_ahead__isnull=False, days_ahead__lte=SEMANAS[-1][1])
        .values("days_ahead")
        .annotate(precio=Avg("min_price_pen"), n=Count("id"))
    }

    grupos = []
    for desde, hasta, etiqueta, corto in SEMANAS:
        dias = [d for d in por_dia if desde <= d <= hasta]
        muestras = sum(por_dia[d][1] for d in dias)
        if not muestras:
            continue
        total = sum(por_dia[d][0] * por_dia[d][1] for d in dias)
        grupos.append((etiqueta, corto, total / muestras, muestras))

    return _armar(grupos)


def cheapest_airlines(route=None, limite: int = 4) -> list[Aerolinea]:
    """Qué aerolínea aparece más veces como la más barata.

    No es «cuál es mejor»: es cuántas veces ganó en precio. La distinción
    importa, porque la respuesta suele contradecir la intuición de que una low
    cost siempre gana.
    """
    filas = list(
        _base(route)
        .exclude(cheapest_airline="")
        .values("cheapest_airline")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    total = sum(f["n"] for f in filas)
    if total < MIN_MUESTRAS_AEROLINEA:
        return []

    return [
        Aerolinea(
            nombre=f["cheapest_airline"],
            veces=f["n"],
            cuota_pct=int(round(f["n"] / total * 100)),
        )
        for f in filas[:limite]
        # Por debajo del 1% es una anécdota: un scrapeo raro, no una aerolínea
        # que compita de verdad en la ruta.
        if round(f["n"] / total * 100) >= 1
    ]
