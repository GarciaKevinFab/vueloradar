"""Páginas «Vuelos a X»: todo lo que llega a una ciudad.

La página nació de Search Console: la familia de consultas más grande era una
sola ciudad («vuelos talara») y ninguna página trataba de volar HACIA un
lugar. El riesgo que estos tests vigilan es el contrario: que con un solo
origen la página sea la ficha repetida, que es lo que se podó de los hubs.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.flights.models import Airport, PriceSnapshot, Route, RouteStats
from apps.web import insights, lectura, queries
from apps.web.views import MAX_ASIMETRIAS


def _publicar(origen, destino, mediana="260", precio="179"):
    """Ruta publicada con mediana propia y un precio vigente."""
    r = Route.objects.create(origin_id=origen, destination_id=destino, is_monitored=True)
    RouteStats.objects.create(
        route=r, avg_30d=Decimal(mediana), min_30d=Decimal("150"),
        p25_30d=Decimal("200"), median_30d=Decimal(mediana), samples_count=40,
    )
    PriceSnapshot.objects.create(
        route=r, flight_date=timezone.localdate() + timedelta(days=10),
        min_price_pen=Decimal(precio), avg_price_pen=Decimal(precio), offers_count=3,
    )
    return r


def _pagina(client, slug):
    return client.get(f"/vuelos/a-{slug}/").content.decode()


# --- la página ----------------------------------------------------------------

def test_lista_los_origenes_que_llegan_a_la_ciudad(client, peru_airports):
    _publicar("LIM", "CUZ")
    _publicar("AQP", "CUZ")
    _publicar("LIM", "PEM")  # llega a otra ciudad: no debe aparecer

    cuerpo = _pagina(client, "cusco")
    assert "Desde Lima" in cuerpo and "Desde Arequipa" in cuerpo
    assert "Desde Puerto Maldonado" not in cuerpo
    assert "Llegan vuelos desde 2 ciudades" in cuerpo


def test_ciudad_a_la_que_no_llega_nada_da_404(client, peru_airports):
    _publicar("LIM", "CUZ")
    assert client.get("/vuelos/a-arequipa/").status_code == 404
    assert client.get("/vuelos/a-narnia/").status_code == 404


def test_la_url_no_se_confunde_con_la_ficha_ni_con_el_hub(client, peru_airports):
    _publicar("LIM", "CUZ")
    assert client.get("/vuelos/a-cusco/").status_code == 200
    assert client.get("/vuelos/LIM-CUZ/").status_code == 200
    assert client.get("/vuelos/desde-lima/").status_code == 200


def test_el_viaje_completo_dice_cual_tramo_es_el_caro(client, peru_airports):
    """Volver desde Juliaca costaba un 63% más que ir: eso es lo que la página
    tiene y ninguna ficha tiene sola."""
    _publicar("LIM", "PEM", mediana="200")
    _publicar("PEM", "LIM", mediana="300")

    cuerpo = _pagina(client, "puerto-maldonado")
    assert "Cuánto cuesta el viaje completo" in cuerpo
    assert "Volver de Puerto Maldonado a Lima cuesta un 50% más que ir" in cuerpo
    assert "S/ 500" in cuerpo  # 200 + 300, las medianas


def test_si_ida_y_vuelta_cuestan_parecido_lo_dice_y_no_inventa_un_tramo_caro(client, peru_airports):
    _publicar("LIM", "PEM", mediana="200")
    _publicar("PEM", "LIM", mediana="210")  # 5%: dentro del ruido

    cuerpo = _pagina(client, "puerto-maldonado")
    assert "Ir y volver cuestan parecido" in cuerpo
    assert "más que ir" not in cuerpo and "más que volver" not in cuerpo


def test_la_prosa_de_asimetrias_no_pasa_del_tope(client, peru_airports):
    """Con Lima como destino son diecisiete orígenes; más frases iguales se
    leen como plantilla."""
    Airport.objects.create(iata_code="JUL", name="Inca Manco Cápac", city="Juliaca", region="Puno")
    Airport.objects.create(iata_code="IQT", name="Secada", city="Iquitos", region="Loreto")
    for origen in ("CUZ", "AQP", "PEM", "JUL", "IQT"):
        _publicar(origen, "LIM", mediana="300")
        _publicar("LIM", origen, mediana="200")

    cuerpo = _pagina(client, "lima")
    assert cuerpo.count("cuesta un 50% más que volver") == MAX_ASIMETRIAS


# --- indexación ---------------------------------------------------------------

def test_con_un_solo_origen_y_sin_vuelta_es_la_ficha_repetida_y_no_se_indexa(client, peru_airports):
    _publicar("LIM", "PEM")
    cuerpo = _pagina(client, "puerto-maldonado")
    assert '<meta name="robots" content="noindex,follow">' in cuerpo
    assert "/vuelos/a-puerto-maldonado/" not in client.get("/sitemap.xml").content.decode()


def test_con_un_solo_origen_pero_con_vuelta_si_se_indexa(client, peru_airports):
    """El caso de 13 de las 18 ciudades reales: solo Lima, pero con vuelta."""
    _publicar("LIM", "PEM")
    _publicar("PEM", "LIM")
    cuerpo = _pagina(client, "puerto-maldonado")
    assert 'name="robots"' not in cuerpo
    assert "/vuelos/a-puerto-maldonado/" in client.get("/sitemap.xml").content.decode()


def test_la_regla_de_indexacion_es_la_misma_en_la_pagina_y_en_el_sitemap(peru_airports):
    ida = _publicar("LIM", "CUZ")
    assert queries.destino_indexable([ida], []) is False
    vuelta = _publicar("CUZ", "LIM")
    assert queries.destino_indexable([ida], [vuelta]) is True
    otra = _publicar("AQP", "CUZ")
    assert queries.destino_indexable([ida, otra], []) is True


def test_el_titulo_no_se_trunca_con_el_peor_nombre_real(client, peru_airports):
    Airport.objects.create(iata_code="JAU", name="Francisco Carlé", city="Jauja",
                           region="Junín", alias="Huancayo")
    _publicar("LIM", "PEM")
    _publicar("LIM", "JAU")
    for slug in ("puerto-maldonado", "jauja"):
        titulo = re.search(r"<title>(.*?)</title>", _pagina(client, slug), re.S).group(1).strip()
        assert len(titulo) <= 63, f"{slug}: {len(titulo)} — {titulo}"
    assert "<title>Vuelos a Jauja (Huancayo): ida y vuelta" in _pagina(client, "jauja")


# --- enlaces hacia la página ---------------------------------------------------

def test_la_portada_la_ficha_y_el_hub_enlazan_la_pagina(client, peru_airports):
    _publicar("LIM", "CUZ")
    _publicar("CUZ", "LIM")

    assert "/vuelos/a-cusco/" in client.get("/").content.decode()
    assert "/vuelos/a-cusco/" in client.get("/vuelos/LIM-CUZ/").content.decode()
    assert "/vuelos/a-cusco/" in client.get("/vuelos/desde-cusco/").content.decode()


# --- publicidad ---------------------------------------------------------------

def test_el_anuncio_usa_su_propio_espacio(client, peru_airports):
    _publicar("LIM", "CUZ")
    with override_settings(ADSENSE_CLIENT="ca-pub-1", ADSENSE_SLOT_DESTINO="555"):
        cuerpo = _pagina(client, "cusco")
    assert 'data-ad-slot="555"' in cuerpo


def test_sin_espacio_configurado_no_dibuja_hueco(client, peru_airports):
    _publicar("LIM", "CUZ")
    with override_settings(ADSENSE_CLIENT="ca-pub-1", ADSENSE_SLOT_DESTINO=""):
        cuerpo = _pagina(client, "cusco")
    assert '<ins class="adsbygoogle"' not in cuerpo


# --- lógica pura ----------------------------------------------------------------

def test_viaje_completo_sin_un_sentido_no_inventa_nada(peru_airports):
    ida = _publicar("LIM", "CUZ")
    assert lectura.viaje_completo(ida, None) is None
    sin_stats = Route.objects.create(origin_id="CUZ", destination_id="LIM", is_monitored=True)
    assert lectura.viaje_completo(ida, sin_stats) is None


@pytest.mark.parametrize("ida,vuelta,esperado", [
    ("200", "300", "vuelta"),   # 50%
    ("300", "200", "ida"),
    ("200", "229", None),       # 14,5%: bajo el umbral
    ("200", "230", "vuelta"),   # 15% justo: se dice
])
def test_el_tramo_caro_usa_el_mismo_umbral_que_la_ficha(ida, vuelta, esperado):
    v = lectura.Viaje("Lima", "Cusco", Decimal(ida), Decimal(vuelta))
    assert v.tramo_caro == esperado
    assert v.total == Decimal(ida) + Decimal(vuelta)


def test_el_analisis_de_la_pagina_cuenta_solo_lo_que_llega(peru_airports):
    llega = _publicar("LIM", "CUZ")
    sale = _publicar("CUZ", "LIM")
    PriceSnapshot.objects.create(
        route=sale, flight_date=timezone.localdate() + timedelta(days=12),
        min_price_pen=Decimal("190"), avg_price_pen=Decimal("190"), offers_count=2,
    )
    base = insights._base(destino="CUZ")
    assert set(base.values_list("route_id", flat=True)) == {llega.pk}
