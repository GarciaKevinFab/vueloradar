"""Conversión de tarifa base a precio final para vuelos domésticos peruanos.

La regla del dominio (CLAUDE.md secc. 3) es que un precio siempre es el final,
con impuestos. Google Flights ya lo entrega así; algunos motores de venta de
aerolíneas publican **tarifa base** y suman los impuestos recién en el
checkout. Comparar los dos sin normalizar hace que el directo parezca 25-30%
más barato y corrompería las alertas.

**La fórmula está verificada contra una observación real** (2026-08-23,
JetSMART LIM-CUZ del 06/09):

    tarifa base   S/ 144,52
    x 1,18 (IGV)  S/ 170,53
    + TUUA        S/  30,47
    ------------------------
    precio final  S/ 201,00   ← exactamente lo que mostraba Google Flights

Nótese que el IGV grava la tarifa, **no** la TUUA: la TUUA se suma después. Ese
orden es lo que hace cuadrar el número al céntimo, y es la razón de que el test
de esta fórmula use esos valores concretos.

**Limitación conocida:** se aplica una sola TUUA, la del aeropuerto de origen.
Alcanza porque los scrapers directos solo cotizan vuelos sin escalas. Un
itinerario con conexión pagaría TUUA en cada embarque y esta función lo
subestimaría.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

CENTS = Decimal("0.01")


def base_fare_to_final(base_fare) -> Decimal:
    """Precio final con impuestos a partir de una tarifa base en soles.

    Args:
        base_fare: tarifa base publicada por la aerolínea, en PEN.

    Returns:
        El precio comparable contra Google Flights, redondeado a céntimos.

    Raises:
        ValueError: si la tarifa es negativa. Un precio negativo es un error de
            parseo, y dejarlo pasar contaminaría el histórico en silencio.
    """
    base = Decimal(base_fare)
    if base < 0:
        raise ValueError(f"tarifa base negativa: {base}")

    con_igv = base * (Decimal("1") + Decimal(settings.IGV_RATE))
    return (con_igv + Decimal(settings.TUUA_NACIONAL_PEN)).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )
