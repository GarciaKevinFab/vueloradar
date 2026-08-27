"""Buscador de la web: interpreta lo que se escribe y arma el link de compra.

**Sin IA, a propósito.** El bot puede usar el router de modelos porque tiene
identidad y cupo diario por usuario. La web recibe tráfico anónimo: cada
visitante sería una llamada a un modelo sin techo de gasto, y el primer script
que la descubra la convierte en una factura. Un parser determinista cubre lo
que la gente realmente escribe —«de lima a cusco el 15 de setiembre»— a costo
cero y sin latencia.

**Tampoco scrapea.** Una búsqueda en vivo son 3-8 s por consulta y, multiplicado
por visitantes, hace que Google bloquee la IP del VPS. Los resultados salen del
histórico que ya tenemos, que además es lo que este sitio hace bien.

Los alias existen porque la gente escribe la ciudad, no el aeropuerto: Huancayo
no tiene aeropuerto propio y su vuelo sale de Jauja.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

#: Ciudades sin aeropuerto propio, o nombres que la gente usa igual.
ALIAS: dict[str, str] = {
    "huancayo": "JAU",
    "machu picchu": "CUZ",
    "machupicchu": "CUZ",
    "valle sagrado": "CUZ",
    "aguas calientes": "CUZ",
    "puno": "JUL",
    "chachapoyas": "RIM",
    "callao": "LIM",
    "jorge chavez": "LIM",
}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DIA_MES_NUM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
_DIA_DE_MES = re.compile(
    r"\b(\d{1,2})\s*(?:de\s+)?(" + "|".join(MESES) + r")\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Consulta:
    """Lo que se pudo entender del texto libre."""

    origen: str | None = None
    destino: str | None = None
    fecha: date | None = None

    @property
    def es_completa(self) -> bool:
        return bool(self.origen and self.destino and self.fecha)


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar sin sorpresas."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def parse_consulta(texto: str, aeropuertos) -> Consulta:
    """Extrae origen, destino y fecha de una frase.

    Args:
        texto: lo que escribió la persona.
        aeropuertos: iterable de `Airport`.
    """
    plano = normalizar(texto)
    if not plano:
        return Consulta()

    origen, destino = _ciudades(plano, aeropuertos)
    return Consulta(origen=origen, destino=destino, fecha=_fecha(plano))


def _ciudades(plano: str, aeropuertos) -> tuple[str | None, str | None]:
    """La primera ciudad mencionada es el origen; la segunda, el destino.

    Se resuelve por posición en el texto y no por las preposiciones «de» y «a»,
    porque «de» aparece también en las fechas («el 15 de setiembre»).
    """
    mapa: dict[str, str] = {}
    for a in aeropuertos:
        mapa[normalizar(a.city)] = a.iata_code
        mapa[normalizar(a.iata_code)] = a.iata_code
    mapa.update(ALIAS)

    # El nombre mas largo primero, para que "puerto maldonado" gane sobre
    # cualquier coincidencia parcial mas corta.
    hallazgos: list[tuple[int, str]] = []
    for nombre in sorted(mapa, key=len, reverse=True):
        match = re.search(rf"\b{re.escape(nombre)}\b", plano)
        if match:
            hallazgos.append((match.start(), mapa[nombre]))

    hallazgos.sort()
    ordenados: list[str] = []
    for _, iata in hallazgos:
        if iata not in ordenados:
            ordenados.append(iata)

    if len(ordenados) < 2:
        return (ordenados[0] if ordenados else None), None

    origen, destino = ordenados[0], ordenados[1]

    # "machu picchu desde arequipa": el "desde" entre ambas ciudades invierte
    # el sentido. Sin esto se leeria como si el viaje empezara en Cusco.
    entre = plano[hallazgos[0][0] : hallazgos[1][0]]
    if " desde " in f" {entre} ":
        origen, destino = destino, origen

    return origen, destino


def _fecha(plano: str) -> date | None:
    """Fecha escrita de cualquiera de las formas habituales."""
    hoy = timezone.localdate()

    if "pasado manana" in plano:
        return hoy + timedelta(days=2)
    if "manana" in plano:
        return hoy + timedelta(days=1)
    if re.search(r"\bhoy\b", plano):
        return hoy

    match = _ISO.search(plano)
    if match:
        return _armar(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _DIA_DE_MES.search(plano)
    if match:
        dia, mes = int(match.group(1)), MESES[match.group(2).lower()]
        return _con_anio_futuro(dia, mes, hoy)

    match = _DIA_MES_NUM.search(plano)
    if match:
        dia, mes = int(match.group(1)), int(match.group(2))
        anio = match.group(3)
        if anio:
            entero = int(anio)
            return _armar(entero + 2000 if entero < 100 else entero, mes, dia)
        return _con_anio_futuro(dia, mes, hoy)

    return None


def _con_anio_futuro(dia: int, mes: int, hoy: date) -> date | None:
    """Sin año explícito se asume el próximo que aún no pasó.

    Quien escribe «15 de enero» en agosto quiere el enero que viene, no el que
    ya pasó.
    """
    candidata = _armar(hoy.year, mes, dia)
    if candidata is None:
        return None
    return candidata if candidata >= hoy else _armar(hoy.year + 1, mes, dia)


def _armar(anio: int, mes: int, dia: int) -> date | None:
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def booking_url(
    origen: str, destino: str, fecha: date, adultos: int = 1, ninos: int = 0
) -> str:
    """Link de compra en Google Flights, con los pasajeros incluidos.

    El conteo de pasajeros va **dentro** del blob `tfs` de la URL, así que no se
    puede pegar como parámetro: hay que reconstruir la query. Se usa el mismo
    constructor que el scraper, sin llegar a scrapear nada.
    """
    from fast_flights import FlightQuery, Passengers, create_query

    query = create_query(
        flights=[
            FlightQuery(
                date=fecha.isoformat(),
                from_airport=origen.upper(),
                to_airport=destino.upper(),
            )
        ],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=max(1, adultos), children=max(0, ninos)),
        language="es",
        currency="PEN",
    )
    return query.url()
