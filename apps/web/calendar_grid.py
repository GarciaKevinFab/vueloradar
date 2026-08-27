"""El calendario de precios como grilla, no como lista.

Cuarenta y cinco fechas en una tabla son 45 filas: en un teléfono eso es un
muro de siete mil píxeles y nadie compara nada. La misma información en una
grilla de siete columnas entra en una pantalla y se lee como cualquier
calendario — que además es la forma en que la gente elige una fecha de viaje.

Las semanas van de lunes a domingo, como se usa en Perú.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@dataclass(frozen=True)
class Celda:
    """Un día con precio observado."""

    day: date
    price: object
    verdict: object

    @property
    def es_barato(self) -> bool:
        return bool(getattr(self.verdict, "should_buy", False))


@dataclass(frozen=True)
class Semana:
    """Siete posiciones; `None` donde no hay dato para ese día."""

    dias: list[Celda | None]

    @property
    def mes(self) -> str:
        """Mes de la primera celda con dato.

        Se calcula acá y no en la plantilla porque una grilla sin referencia de
        mes obliga a adivinar en qué parte del año está uno.
        """
        con_dato = [c for c in self.dias if c is not None]
        return MESES[con_dato[0].day.month - 1] if con_dato else ""


def build(fechas) -> list[Semana]:
    """Agrupa las fechas con veredicto en semanas de lunes a domingo.

    Args:
        fechas: dicts con `day`, `price` y `verdict`.
    """
    datos = list(fechas)
    if not datos:
        return []

    por_dia = {f["day"]: Celda(f["day"], f["price"], f["verdict"]) for f in datos}
    primero, ultimo = min(por_dia), max(por_dia)

    # Se arranca el lunes de la primera semana y se termina el domingo de la
    # ultima, para que las columnas siempre caigan bajo el mismo dia.
    cursor = primero - timedelta(days=primero.weekday())
    fin = ultimo + timedelta(days=6 - ultimo.weekday())

    semanas: list[Semana] = []
    while cursor <= fin:
        dias = [por_dia.get(cursor + timedelta(days=i)) for i in range(7)]
        if any(d is not None for d in dias):
            semanas.append(Semana(dias=dias))
        cursor += timedelta(days=7)

    return semanas


def meses_visibles(semanas: list[Semana]) -> list[str]:
    """Nombres de los meses que abarca la grilla, sin repetir."""
    vistos: list[str] = []
    for semana in semanas:
        for celda in semana.dias:
            if celda is None:
                continue
            nombre = MESES[celda.day.month - 1]
            if nombre not in vistos:
                vistos.append(nombre)
    return vistos
