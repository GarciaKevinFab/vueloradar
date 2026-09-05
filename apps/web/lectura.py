"""Lo que se puede decir de UNA ruta y de ninguna otra.

Las cuarenta fichas compartían el 79% del vocabulario. No era por falta de
datos: ya traían los tres bloques de análisis. Era que todas decían lo mismo
con otros números, y una plantilla rellenada sigue siendo una plantilla por
muchos decimales que cambien.

Este módulo no añade otro hueco. Mira el perfil de la ruta y **elige qué
contar**, así que dos rutas con perfiles distintos producen párrafos distintos
en estructura, no solo en cifras. Una ruta cuyo precio no se mueve merece el
consejo contrario al de una que se mueve un 40%, y hasta ahora ambas recibían
el mismo.

Medido sobre los datos reales del 2026-09-05, los tres ejes discriminan:

- volatilidad de 0% (LIM-JAU, IQT-LIM, LIM-HUU) a 40% (LIM-CUZ);
- asimetría con la ruta inversa de 12% a 68% (volver desde Juliaca cuesta un
  63% más que ir);
- fechas baratas de 10 sobre 25 a 35 sobre 45.

Regla heredada del resto del proyecto: sin muestras suficientes no se dice
nada. Una observación es una afirmación sobre el mercado, y afirmarla con seis
días de historia sería inventar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: Días de serie mínimos para leer el perfil de una ruta. Por debajo, el rango
#: observado dice más del azar del muestreo que del comportamiento del precio.
MIN_DIAS_SERIE = 10

#: Por debajo de esto el precio se considera quieto. No es cero a propósito: dos
#: soles de diferencia en un mes son ruido de redondeo, no movimiento.
QUIETO_PCT = 8

#: A partir de acá vale la pena decir que se mueve. Entre ambos umbrales la
#: ruta no tiene nada notable que contar y el bloque no se dibuja: es preferible
#: callar a rellenar con una observación tibia.
MOVIDO_PCT = 25

#: Diferencia con la ruta inversa que merece mencionarse. Por debajo de un 15%
#: entra dentro de lo que cambia solo por el día de la semana en que se mire.
ASIMETRIA_PCT = 15

#: Proporción de fechas próximas a buen precio que define el momento.
MOMENTO_BUENO = 0.6
MOMENTO_MALO = 0.2

#: Cuántas observaciones se publican como mucho. Tres es lo que cabe sin que la
#: ficha se vuelva un muro; más allá, la cuarta siempre es la más débil.
MAX_OBSERVACIONES = 3


@dataclass(frozen=True)
class Observacion:
    """Un hecho sobre esta ruta, con su titular y su explicación.

    `clave` existe para los tests y para el CSS: permite afirmar *qué* se
    eligió decir, que es lo que distingue una ficha de otra.
    """

    clave: str
    titular: str
    detalle: str


def _pct(parte: Decimal, total: Decimal) -> int:
    return round(float(parte / total) * 100) if total else 0


def _volatilidad(historia, stats) -> tuple[int, Decimal, Decimal] | None:
    """Cuánto se movió el precio, en porcentaje de la mediana de la ruta."""
    precios = [d.price for d in historia if d.price is not None]
    if len(precios) < MIN_DIAS_SERIE or not stats or not stats.median_30d:
        return None
    barato, caro = min(precios), max(precios)
    return _pct(caro - barato, stats.median_30d), barato, caro


def _observacion_movimiento(route, historia, stats) -> Observacion | None:
    medida = _volatilidad(historia, stats)
    if medida is None:
        return None
    amplitud, barato, caro = medida
    dias = len([d for d in historia if d.price is not None])

    if amplitud <= QUIETO_PCT:
        return Observacion(
            clave="quieto",
            titular="El precio de esta ruta casi no se mueve",
            detalle=(
                f"En los últimos {dias} días el precio más barato varió entre "
                f"S/ {barato:.0f} y S/ {caro:.0f}. Con un margen así, esperar a "
                f"que baje no ha servido de nada: si la fecha te sirve y el "
                f"precio te alcanza, comprar hoy o en dos semanas da "
                f"prácticamente lo mismo."
            ),
        )
    if amplitud >= MOVIDO_PCT:
        return Observacion(
            clave="movido",
            titular="Acá el precio sí se mueve, y bastante",
            detalle=(
                f"En {dias} días fue de S/ {barato:.0f} a S/ {caro:.0f}: un "
                f"{amplitud}% de diferencia sobre el precio habitual de la ruta. "
                f"Es de las que conviene vigilar unos días antes de comprar, "
                f"porque la misma fecha puede costar bastante menos la semana "
                f"que viene."
            ),
        )
    return None


def _observacion_vuelta(route, stats, inversa) -> Observacion | None:
    """La diferencia entre ir y volver, que casi nadie mira antes de comprar."""
    if inversa is None or not stats or not stats.median_30d:
        return None
    stats_inv = getattr(inversa, "stats", None)
    if stats_inv is None or not stats_inv.median_30d:
        return None

    ida, vuelta = stats.median_30d, stats_inv.median_30d
    diferencia = _pct(abs(vuelta - ida), min(ida, vuelta))
    if diferencia < ASIMETRIA_PCT:
        return None

    origen = route.origin.city
    destino = route.destination.city
    if vuelta > ida:
        return Observacion(
            clave="vuelta-cara",
            titular=f"Volver desde {destino} cuesta más que ir",
            detalle=(
                f"La mediana de {origen} a {destino} está en S/ {ida:.0f}, y la "
                f"de {destino} a {origen} en S/ {vuelta:.0f}: un {diferencia}% "
                f"más. Si el viaje es de ida y vuelta, el tramo de regreso es el "
                f"que conviene mirar con tiempo — es donde se va la diferencia."
            ),
        )
    return Observacion(
        clave="ida-cara",
        titular="El tramo caro es la ida, no la vuelta",
        detalle=(
            f"Ir de {origen} a {destino} tiene una mediana de S/ {ida:.0f}, y "
            f"volver S/ {vuelta:.0f}: un {diferencia}% menos. Al revés de lo que "
            f"uno esperaría, acá el tramo que hay que cazar barato es este."
        ),
    )


def _observacion_momento(route, fechas, stats) -> Observacion | None:
    """Si la ruta está hoy en un momento bueno o malo, comparada consigo misma."""
    if not fechas or not stats or not stats.p25_30d:
        return None
    baratas = len([f for f in fechas if f["verdict"].should_buy])
    proporcion = baratas / len(fechas)

    if proporcion >= MOMENTO_BUENO:
        return Observacion(
            clave="momento-bueno",
            titular="La ruta está barata en casi todo el calendario",
            detalle=(
                f"{baratas} de las {len(fechas)} fechas que seguimos están hoy "
                f"por debajo de lo que suele costar esta ruta. Cuando pasa esto "
                f"no hay mucho que esperar: la caída ya ocurrió y lo que queda "
                f"es elegir el día que te sirva."
            ),
        )
    if proporcion <= MOMENTO_MALO:
        # «Solo 0 de 45» es lo que salió publicado en LIM-HUU: el cero merece
        # su propia frase, no un "solo" que lo trata como si fuera poco.
        if baratas == 0:
            cuantas = f"Ninguna de las {len(fechas)} fechas que seguimos está"
            titular = "Hoy ninguna fecha está a buen precio"
        else:
            cuantas = f"Solo {baratas} de {len(fechas)} fechas están"
            titular = "Hoy casi ninguna fecha está a buen precio"
        return Observacion(
            clave="momento-malo",
            titular=titular,
            detalle=(
                f"{cuantas} por debajo del precio habitual de la ruta. Si el "
                f"viaje puede esperar, esta no es la semana para comprarlo; si "
                f"no puede, al menos ya sabes que estás pagando por encima de "
                f"lo normal."
            ),
        )
    return None


def leer_ruta(route, historia, fechas, stats, inversa=None) -> list[Observacion]:
    """Qué tiene esta ruta de particular, dicho con sus propios datos.

    El orden es el de utilidad para quien está por comprar: primero si el
    precio se mueve (decide si esperar sirve), después la asimetría con la
    vuelta (decide qué tramo vigilar) y por último el momento actual.

    Devuelve lista vacía cuando no hay nada notable, y eso es una respuesta
    válida: una ruta sin particularidades no necesita que le inventemos una.
    """
    candidatas = [
        _observacion_movimiento(route, historia, stats),
        _observacion_vuelta(route, stats, inversa),
        _observacion_momento(route, fechas, stats),
    ]
    return [o for o in candidatas if o is not None][:MAX_OBSERVACIONES]
