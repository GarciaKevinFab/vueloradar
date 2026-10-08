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


@dataclass(frozen=True)
class Viaje:
    """Lo que cuesta ir y volver entre dos ciudades, en precios típicos.

    Usa la MEDIANA de 30 días de cada sentido, no el mínimo vigente. Sumar el
    mínimo de la ida y el de la vuelta daría un viaje que no existe: el día más
    barato para volver puede caer antes que el día más barato para ir. La
    mediana sí se puede sumar con honestidad, porque es lo que suele costar
    cada tramo.
    """

    origen: str
    destino: str
    ida: Decimal
    vuelta: Decimal

    @property
    def total(self) -> Decimal:
        return self.ida + self.vuelta

    @property
    def diferencia_pct(self) -> int:
        return _pct(abs(self.vuelta - self.ida), min(self.ida, self.vuelta))

    @property
    def tramo_caro(self) -> str | None:
        """'ida' o 'vuelta' si la diferencia merece decirse; None si no.

        Mismo umbral que la observación de la ficha: por debajo de un 15% la
        diferencia cambia sola según el día de la semana en que se mire.
        """
        if self.diferencia_pct < ASIMETRIA_PCT:
            return None
        return "vuelta" if self.vuelta > self.ida else "ida"


def viaje_completo(ida, vuelta) -> Viaje | None:
    """El viaje de ida y vuelta entre las dos rutas, o None si falta un sentido.

    Sin estadísticas de alguno de los dos tramos no se inventa nada: la página
    simplemente no muestra esa fila del viaje completo.
    """
    if ida is None or vuelta is None:
        return None
    stats_ida = getattr(ida, "stats", None)
    stats_vuelta = getattr(vuelta, "stats", None)
    if not stats_ida or not stats_vuelta:
        return None
    if not stats_ida.median_30d or not stats_vuelta.median_30d:
        return None
    return Viaje(
        origen=ida.origin.city,
        destino=ida.destination.city,
        ida=stats_ida.median_30d,
        vuelta=stats_vuelta.median_30d,
    )


# --- Viajes parejos: cuando ida y vuelta cuestan lo mismo ----------------------
#
# La página «Vuelos a X» decía lo mismo en 13 de las 18 ciudades: «Ir y volver
# cuestan parecido». Era cierto y no servía, porque no distinguía Talara de
# Tumbes. Medido en producción el 2026-10-07, lo que sí las separa es el
# puesto del viaje completo entre los destinos del mismo origen (de 1 a 17,
# uno distinto para cada ciudad), qué tramo tiene hoy más fechas baratas
# (5 ciudades con 15 puntos o más de diferencia) y si el precio baja o no
# (Huánuco y Andahuaylas casi no bajan; las demás rondan el 40%).

#: Diferencia entre la proporción de fechas baratas de la ida y la de la
#: vuelta a partir de la cual se dice qué tramo cerrar primero. Medido: CUZ 18
#: puntos, CIX 20, TBP 30, IQT 31; AQP 13 y PIU 9 quedan fuera.
MOMENTO_DESIGUAL = 0.15

#: Rebaja máxima de 30 días (mínimo contra mediana) por debajo de la cual un
#: tramo se considera rígido. Huánuco 3% y 9%, Andahuaylas 20% y 3%; el resto
#: de las rutas desde Lima va de 32% a 62%.
RIGIDO_PCT = 25

#: Rebaja que merece contarse aunque el viaje sea parejo. Solo Puerto
#: Maldonado (62% en la vuelta) la supera entre los viajes parejos.
REBAJA_PCT = 55

#: Con menos destinos que esto, decir «el 2.º más barato» no informa nada.
MIN_DESTINOS_PUESTO = 3


@dataclass(frozen=True)
class Tramo:
    """Un sentido del viaje: lo que suele costar y cómo está hoy."""

    mediana: Decimal
    minimo: Decimal | None
    baratas: int
    fechas: int

    @property
    def proporcion(self) -> float | None:
        return self.baratas / self.fechas if self.fechas else None

    @property
    def rebaja_pct(self) -> int | None:
        if self.minimo is None or not self.mediana:
            return None
        return _pct(self.mediana - self.minimo, self.mediana)


def tramo_de(route, resumen: dict) -> Tramo | None:
    """El tramo de una ruta con su resumen de fechas, o None sin mediana."""
    stats = getattr(route, "stats", None) if route is not None else None
    if not stats or not stats.median_30d:
        return None
    return Tramo(
        mediana=stats.median_30d,
        minimo=stats.min_30d,
        baratas=resumen.get("baratas", 0),
        fechas=resumen.get("fechas", 0),
    )


@dataclass(frozen=True)
class Puesto:
    """Dónde cae un viaje completo entre todos los del mismo origen."""

    n: int
    de: int
    origen: str


def puesto_del_viaje(rutas, origen: str, destino: str) -> Puesto | None:
    """Puesto del viaje origen-destino entre los viajes completos del origen.

    Se calcula sobre las rutas ya cargadas, sin consultas: cada ruta publicada
    trae sus estadísticas. Solo cuentan los destinos con ida y vuelta
    publicadas, que son los únicos que tienen viaje completo.
    """
    pares = {(r.origin_id, r.destination_id): r for r in rutas}
    totales = {}
    for (o, d), ida in pares.items():
        if o != origen or (d, o) not in pares:
            continue
        viaje = viaje_completo(ida, pares[(d, o)])
        if viaje is not None:
            totales[d] = viaje.total
    if destino not in totales or len(totales) < MIN_DESTINOS_PUESTO:
        return None
    orden = sorted(totales, key=lambda d: (totales[d], d))
    nombre = pares[(origen, destino)].origin.city
    return Puesto(n=orden.index(destino) + 1, de=len(orden), origen=nombre)


def _n_destinos(n: int) -> str:
    return "1 destino" if n == 1 else f"{n} destinos"


def _concuerda(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _observacion_momento_viaje(ida: Tramo, vuelta: Tramo) -> Observacion | None:
    pi, pv = ida.proporcion, vuelta.proporcion
    if pi is None or pv is None:
        return None

    if pi >= MOMENTO_BUENO and pv >= MOMENTO_BUENO:
        return Observacion(
            clave="ambos-baratos",
            titular="Los dos tramos están baratos a la vez",
            detalle=(
                f"{ida.baratas} de {ida.fechas} fechas de ida y {vuelta.baratas} "
                f"de {vuelta.fechas} de vuelta están hoy por debajo de lo "
                f"habitual. Que coincidan pasa pocas veces: si ya tienes las "
                f"fechas, es buen momento para comprar el viaje completo."
            ),
        )
    if pi <= MOMENTO_MALO and pv <= MOMENTO_MALO:
        if ida.baratas == 0 and vuelta.baratas == 0:
            cuantas = (
                f"Ninguna de las {ida.fechas} fechas de ida ni de las "
                f"{vuelta.fechas} de vuelta está"
            )
        else:
            cuantas = (
                f"Solo {ida.baratas} de {ida.fechas} fechas de ida y "
                f"{vuelta.baratas} de {vuelta.fechas} de vuelta están"
            )
        return Observacion(
            clave="ambos-caros",
            titular="Hoy ningún tramo está a buen precio",
            detalle=(
                f"{cuantas} por debajo de lo habitual. Si el viaje puede "
                f"esperar, esta no es la semana para comprarlo."
            ),
        )
    if abs(pi - pv) >= MOMENTO_DESIGUAL:
        if pi > pv:
            primero, segundo, t1, t2 = "ida", "vuelta", ida, vuelta
        else:
            primero, segundo, t1, t2 = "vuelta", "ida", vuelta, ida
        return Observacion(
            clave=f"cerrar-{primero}",
            titular=f"Hoy conviene cerrar primero la {primero}",
            detalle=(
                f"La {primero} tiene {t1.baratas} de {t1.fechas} fechas a buen "
                f"precio y la {segundo} solo {t2.baratas} de {t2.fechas}. En un "
                f"mes normal los dos tramos cuestan casi lo mismo, así que la "
                f"diferencia de hoy es la oportunidad: asegura la {primero} y "
                f"deja la {segundo} en observación unos días."
            ),
        )
    return None


def _observacion_rebaja_viaje(viaje: Viaje, ida: Tramo, vuelta: Tramo) -> Observacion | None:
    ri, rv = ida.rebaja_pct, vuelta.rebaja_pct
    if ri is None or rv is None:
        return None

    if ri < RIGIDO_PCT and rv < RIGIDO_PCT:
        return Observacion(
            clave="rigido",
            titular="Este viaje casi nunca baja",
            detalle=(
                f"En 30 días lo más barato de la ida fue S/ {ida.minimo:.0f}, "
                f"contra S/ {ida.mediana:.0f} de lo habitual, y la vuelta "
                f"S/ {vuelta.minimo:.0f} contra S/ {vuelta.mediana:.0f}. Con tan "
                f"poco margen, esperar una oferta no suele pagar: compra cuando "
                f"tengas la fecha."
            ),
        )
    if max(ri, rv) >= REBAJA_PCT:
        if rv >= ri:
            sentido, tramo = "vuelta", vuelta
            desde, hacia = viaje.destino, viaje.origen
        else:
            sentido, tramo = "ida", ida
            desde, hacia = viaje.origen, viaje.destino
        return Observacion(
            clave=f"rebaja-{sentido}",
            titular=f"La {sentido} llegó a costar un {tramo.rebaja_pct}% menos de lo habitual",
            detalle=(
                f"En 30 días el tramo de {desde} a {hacia} tocó "
                f"S/ {tramo.minimo:.0f}, cuando lo normal es S/ {tramo.mediana:.0f}. "
                f"Ida y vuelta suelen costar lo mismo, pero las caídas grandes "
                f"las da ese tramo: es el que vale la pena vigilar."
            ),
        )
    return None


def _observacion_puesto(viaje: Viaje, puesto: Puesto) -> Observacion:
    n, de, origen = puesto.n, puesto.de, puesto.origen
    if n == 1:
        titular = f"El viaje completo más barato desde {origen}"
    elif n == de:
        titular = f"El viaje completo más caro desde {origen}"
    elif n <= de / 2:
        titular = f"El {n}.º viaje completo más barato desde {origen}"
    else:
        titular = f"El {de - n + 1}.º viaje completo más caro desde {origen}"

    total = f"S/ {viaje.total:.0f}"
    ida = f"S/ {viaje.ida:.0f}"
    vuelta = f"S/ {viaje.vuelta:.0f}"
    antes, despues = n - 1, de - n

    if n <= de / 3:
        if antes == 0:
            cuantos = "ninguno sale más barato"
        else:
            cuantos = (
                f"solo {_n_destinos(antes)} "
                f"{_concuerda(antes, 'sale más barato', 'salen más baratos')}"
            )
        detalle = (
            f"Ir y volver suman {total}: de los {de} destinos que seguimos "
            f"desde {origen}, {cuantos}. La ida ({ida}) y la vuelta ({vuelta}) "
            f"cuestan casi lo mismo, así que el orden en que compres los "
            f"tramos da igual."
        )
    elif n > de * 2 / 3:
        if despues == 0:
            cuantos = "ninguno cuesta más"
        else:
            cuantos = (
                f"solo {_n_destinos(despues)} "
                f"{_concuerda(despues, 'cuesta', 'cuestan')} más"
            )
        detalle = (
            f"Ida y vuelta suman {total}: de los {de} destinos que seguimos "
            f"desde {origen}, {cuantos}. No hay un tramo culpable, la ida "
            f"({ida}) y la vuelta ({vuelta}) cuestan casi lo mismo: lo caro es "
            f"la ruta entera."
        )
    else:
        detalle = (
            f"Con {total} ida y vuelta queda a mitad de tabla: desde {origen}, "
            f"{_n_destinos(antes)} "
            f"{_concuerda(antes, 'sale más barato', 'salen más baratos')} y "
            f"{_n_destinos(despues)} {_concuerda(despues, 'más caro', 'más caros')}. "
            f"Entre la ida y la vuelta hay menos de un 15% de diferencia, así "
            f"que no hay un tramo caro que vigilar."
        )
    return Observacion(clave="puesto", titular=titular, detalle=detalle)


def leer_viaje_parejo(
    viaje: Viaje, ida: Tramo | None, vuelta: Tramo | None, puesto: Puesto | None
) -> list[Observacion]:
    """Qué contar de un viaje cuyos dos tramos cuestan lo mismo.

    Misma regla que `leer_ruta`: elige por el perfil y calla lo que no es
    notable. El orden es el de utilidad para quien compra: qué tramo cerrar
    hoy, si vale la pena esperar una rebaja, y dónde cae el viaje entre los
    demás del mismo origen. Lista vacía si no hay nada que decir; la página
    cae entonces a la frase genérica.
    """
    candidatas = []
    if ida is not None and vuelta is not None:
        candidatas.append(_observacion_momento_viaje(ida, vuelta))
        candidatas.append(_observacion_rebaja_viaje(viaje, ida, vuelta))
    if puesto is not None:
        candidatas.append(_observacion_puesto(viaje, puesto))
    return [o for o in candidatas if o is not None][:MAX_OBSERVACIONES]
