"""Hallazgos del histórico: qué día volar, cuándo comprar y quién gana.

Lo que se prueba acá no es la aritmética del promedio —eso lo hace la base—
sino la regla que sostiene la credibilidad del sitio: **no opinar sin muestras
suficientes**. Un "los miércoles son más baratos" con doce observaciones es
indistinguible de una invención, y es lo único que este producto tiene para
ofrecer.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.flights.models import PriceSnapshot, Route
from apps.web import insights


@pytest.fixture
def route(peru_airports):
    return Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True)


def _snaps(route, *, cuantos, precio, dia_semana=None, dias_antes=10, aerolinea=""):
    """Crea snapshots con la fecha de vuelo calzada en un día de semana concreto.

    `flight_date` manda: el análisis por día mira el día del VUELO, no el de la
    observación.
    """
    hoy = timezone.localdate()
    fecha = hoy + timedelta(days=dias_antes)
    if dia_semana is not None:
        # `weekday()` es 0=lunes. Se corre hacia adelante hasta caer en el día
        # pedido, sin mover la anticipación más de una semana.
        fecha += timedelta(days=(dia_semana - fecha.weekday()) % 7)

    PriceSnapshot.objects.bulk_create([
        PriceSnapshot(
            route=route, flight_date=fecha,
            min_price_pen=Decimal(precio), avg_price_pen=Decimal(precio),
            offers_count=3, cheapest_airline=aerolinea,
            days_ahead=(fecha - hoy).days,
        )
        for _ in range(cuantos)
    ])


# --- la regla que sostiene todo ---------------------------------------------

def test_no_opina_sin_historico(route):
    _snaps(route, cuantos=5, precio="200")
    assert insights.weekday_prices() is None
    assert insights.booking_windows() is None


def test_no_opina_con_menos_de_tres_grupos(route):
    """Dos puntos son una anécdota, no una distribución."""
    _snaps(route, cuantos=200, precio="200", dia_semana=0)
    _snaps(route, cuantos=200, precio="300", dia_semana=2)
    assert insights.weekday_prices() is None


def test_descarta_los_grupos_con_pocas_muestras(route):
    """Un viernes con tres observaciones no puede ser 'el día más barato'."""
    for dia in (0, 1, 2):
        _snaps(route, cuantos=150, precio="300", dia_semana=dia)
    _snaps(route, cuantos=3, precio="50", dia_semana=4)   # viernes anecdótico

    hallazgo = insights.weekday_prices()
    assert "viernes" not in [b.etiqueta for b in hallazgo.barras]


# --- el hallazgo -------------------------------------------------------------

def test_encuentra_el_dia_mas_barato(route):
    _snaps(route, cuantos=150, precio="400", dia_semana=6)   # domingo
    _snaps(route, cuantos=150, precio="300", dia_semana=0)   # lunes
    _snaps(route, cuantos=150, precio="200", dia_semana=2)   # miércoles

    h = insights.weekday_prices()
    assert h.mejor.etiqueta == "miércoles"
    assert h.peor.etiqueta == "domingos"
    assert h.ahorro_pct == 50            # de 400 a 200
    assert h.ahorro_pen == Decimal("200")


def test_la_etiqueta_larga_del_dia_va_en_plural(route):
    """La plantilla escribe "los {etiqueta}": en singular publicaba "los sábado".

    De lunes a viernes el plural es idéntico al singular, así que el defecto
    sólo se veía cuando el día más caro caía sábado o domingo — y salía en la
    portada y en las 40 fichas a la vez.
    """
    _snaps(route, cuantos=150, precio="400", dia_semana=5)   # sábado
    _snaps(route, cuantos=150, precio="300", dia_semana=6)   # domingo
    _snaps(route, cuantos=150, precio="200", dia_semana=0)   # lunes

    etiquetas = [b.etiqueta for b in insights.weekday_prices().barras]
    assert "sábados" in etiquetas
    assert "domingos" in etiquetas
    assert "lunes" in etiquetas          # el plural no le agrega una "s" de más


def test_una_diferencia_menor_al_5_por_ciento_no_es_titular(route):
    """Ruido estadístico presentado como consejo es peor que no decir nada."""
    _snaps(route, cuantos=150, precio="300", dia_semana=0)
    _snaps(route, cuantos=150, precio="302", dia_semana=2)
    _snaps(route, cuantos=150, precio="305", dia_semana=4)

    assert insights.weekday_prices().vale_la_pena is False


def test_la_barra_mas_barata_sigue_siendo_visible(route):
    """El alto se mide sobre el rango, con un piso: una barra de alto 0 no se ve."""
    _snaps(route, cuantos=150, precio="400", dia_semana=6)
    _snaps(route, cuantos=150, precio="300", dia_semana=0)
    _snaps(route, cuantos=150, precio="200", dia_semana=2)

    altos = [b.alto_pct for b in insights.weekday_prices().barras]
    assert min(altos) >= 20
    assert max(altos) == 100


def test_cada_barra_trae_etiqueta_corta(route):
    """En un teléfono de 375 px no entran siete columnas de 'miércoles'."""
    for dia in (0, 2, 4):
        _snaps(route, cuantos=150, precio="300", dia_semana=dia)
    assert all(len(b.corto) <= 8 for b in insights.weekday_prices().barras)


# --- anticipación ------------------------------------------------------------

def test_encuentra_la_ventana_de_compra(route):
    _snaps(route, cuantos=150, precio="400", dias_antes=2)    # menos de una semana
    _snaps(route, cuantos=150, precio="250", dias_antes=10)   # 1 a 2 semanas
    _snaps(route, cuantos=150, precio="320", dias_antes=20)   # 2 a 4 semanas

    h = insights.booking_windows()
    assert h.mejor.etiqueta == "1 a 2 semanas"
    assert h.vale_la_pena is True


def test_las_franjas_ignoran_los_snapshots_sin_anticipacion(route):
    """Las filas anteriores al backfill tienen `days_ahead` en NULL."""
    _snaps(route, cuantos=400, precio="300", dias_antes=10)
    PriceSnapshot.objects.update(days_ahead=None)
    assert insights.booking_windows() is None


# --- aerolíneas --------------------------------------------------------------

def test_cuenta_cuantas_veces_gano_cada_aerolinea(route):
    _snaps(route, cuantos=75, precio="300", aerolinea="LATAM")
    _snaps(route, cuantos=25, precio="280", aerolinea="JetSMART")

    cuotas = {a.nombre: a.cuota_pct for a in insights.cheapest_airlines()}
    assert cuotas == {"LATAM": 75, "JetSMART": 25}


def test_una_aerolinea_anecdotica_no_aparece(route):
    """Una aparición suelta es un scrapeo raro, no un competidor de la ruta."""
    _snaps(route, cuantos=200, precio="300", aerolinea="LATAM")
    _snaps(route, cuantos=1, precio="280", aerolinea="Sky")

    assert [a.nombre for a in insights.cheapest_airlines()] == ["LATAM"]


def test_sin_muestras_suficientes_no_nombra_aerolineas(route):
    _snaps(route, cuantos=10, precio="300", aerolinea="LATAM")
    assert insights.cheapest_airlines() == []


# --- por ruta ----------------------------------------------------------------

def test_el_analisis_por_ruta_ignora_las_demas(peru_airports):
    """El mejor día para volar a Cusco no tiene por qué serlo para Arequipa."""
    cusco = Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True)
    arequipa = Route.objects.create(origin_id="LIM", destination_id="AQP", is_monitored=True)

    _snaps(cusco, cuantos=150, precio="200", dia_semana=2)    # miércoles barato
    _snaps(cusco, cuantos=150, precio="400", dia_semana=0)
    _snaps(cusco, cuantos=150, precio="380", dia_semana=4)
    _snaps(arequipa, cuantos=150, precio="500", dia_semana=2)  # miércoles caro
    _snaps(arequipa, cuantos=150, precio="200", dia_semana=0)
    _snaps(arequipa, cuantos=150, precio="480", dia_semana=4)

    assert insights.weekday_prices(cusco).mejor.etiqueta == "miércoles"
    assert insights.weekday_prices(arequipa).mejor.etiqueta == "lunes"
