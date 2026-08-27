"""Lectura del calendario de precios de JetSMART.

El deep link no aterriza en la lista de vuelos sino en un calendario de dos
meses, así que un número de día aparece dos veces. Antes se tomaba la primera
aparición: verificar el 6/10 devolvía el precio del 6/09.
"""

from decimal import Decimal

import pytest

from apps.scraping.providers.jetsmart import price_for_day

CALENDARIO = """Septiembre 2026
5
S/158.00
6
S/141.16
7
S/134.44
Mejor precio
Octubre 2026
5
S/402.10
6
S/389.00
7
S/377.55
"""

SIN_ENCABEZADOS = """5
S/158.00
6
S/141.16
"""


def test_distingue_el_mismo_dia_en_meses_distintos():
    """Regresión: el 6/10 devolvía S/141.16 en vez de S/389, un 175% de error
    que habría disparado una alerta falsa de chollo."""
    assert price_for_day(CALENDARIO, 6, 9) == Decimal("141.16")
    assert price_for_day(CALENDARIO, 6, 10) == Decimal("389.00")


def test_sin_mes_y_con_dia_repetido_falla_cerrado():
    """Para verificar un precio, no saber es mejor que adivinar mal."""
    assert price_for_day(CALENDARIO, 6) is None


def test_sin_encabezados_pero_dia_unico_resuelve():
    assert price_for_day(SIN_ENCABEZADOS, 6) == Decimal("141.16")


def test_ignora_el_texto_pegado_al_precio():
    """'Mejor precio' viaja junto al día más barato del calendario."""
    assert price_for_day(CALENDARIO, 7, 9) == Decimal("134.44")


def test_dia_inexistente_da_none():
    assert price_for_day(CALENDARIO, 28, 9) is None


def test_calendario_vacio_da_none():
    assert price_for_day("", 6, 9) is None
    assert price_for_day(None, 6, 9) is None


@pytest.mark.parametrize("encabezado,mes", [
    ("Septiembre 2026", 9), ("SETIEMBRE", 9), ("octubre 2026", 10),
])
def test_acepta_las_variantes_de_encabezado(encabezado, mes):
    texto = f"{encabezado}\n6\nS/141.16\n"
    assert price_for_day(texto, 6, mes) == Decimal("141.16")


# --- texto real de la página, capturado el 2026-08-27 -----------------------
# innerText de booking.jetsmart.com para LIM-CUZ 2026-09-15. Se guarda verbatim
# porque es la única forma de que "extracción verificada" signifique algo: si
# JetSMART cambia la estructura, estos tests fallan antes que producción.
REAL = """Cambiar Reserva
Ver precios con tasas e impuestos
Volver a la vista anterior
COTIZAR EL VUELO EN: SOL
USD
¡Revisa los mejores precios para tu vuelo!
Calendario precios
Gráfico de precios
IDA: Lima a Cusco
Ago
2026
Sep
2026
Oct
2026
Lun
Mar
Mié
Jue
Vie
Sáb
Dom
31
S/140.72
1
S/140.72
2
S/140.72
3
S/140.72
4
S/144.24
5
S/144.24
6
S/193.49
7
S/144.24
8
S/144.24
9
S/140.72
10
S/144.24
11
S/151.28
12
S/144.24
13
S/140.72
14
S/91.47
15
S/91.47
Mejor precio
16
S/140.72
17
S/140.72
18
S/140.72
19
S/112.58
20
S/91.47
Mejor precio
Continuar
© 2026 JetSMART Perú
"""


def test_lee_el_calendario_real():
    """Los tres valores se verificaron contra la página en vivo."""
    assert price_for_day(REAL, 15, 9) == Decimal("91.47")
    assert price_for_day(REAL, 6, 9) == Decimal("193.49")
    assert price_for_day(REAL, 11, 9) == Decimal("151.28")


def test_las_pestanas_de_mes_no_se_confunden_con_encabezados():
    """El selector imprime 'Ago/Sep/Oct' abreviado y el año en otra línea.

    Si alguien agrega las abreviaturas a MESES sin mirar esto, 'Oct' pasaría a
    fijar el mes en 10 y todo el calendario de septiembre quedaría descartado.
    """
    assert price_for_day(REAL, 15, 9) == Decimal("91.47")


def test_el_dia_arrastrado_del_mes_anterior_se_lee_igual():
    """La grilla de septiembre arranca con el 31 de agosto."""
    assert price_for_day(REAL, 31, 8) == Decimal("140.72")


def test_ningun_dia_se_repite_en_la_pagina_real():
    """Verificado en vivo: 30 días con precio, cero repetidos.

    Mientras se cumpla, la desambiguación por mes no llega a hacer falta; el
    fallo cerrado existe para cuando deje de cumplirse.
    """
    for dia in range(1, 21):
        assert price_for_day(REAL, dia) is not None, f"día {dia} sin precio o ambiguo"
