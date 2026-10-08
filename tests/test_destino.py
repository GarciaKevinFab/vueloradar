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
    assert "S/&nbsp;500" in cuerpo  # 200 + 300, las medianas


def test_un_viaje_parejo_no_inventa_un_tramo_caro_y_dice_algo_propio(client, peru_airports):
    _publicar("LIM", "PEM", mediana="200")
    _publicar("PEM", "LIM", mediana="210")  # 5%: dentro del ruido

    cuerpo = _pagina(client, "puerto-maldonado")
    assert "más que ir" not in cuerpo and "más que volver" not in cuerpo
    # Con un solo precio vigente por tramo, y bajo el p25, los dos están baratos.
    assert "Los dos tramos están baratos a la vez" in cuerpo
    assert "Ir y volver cuestan parecido" not in cuerpo


def _caro(route, precio="400", dias=11):
    PriceSnapshot.objects.create(
        route=route, flight_date=timezone.localdate() + timedelta(days=dias),
        min_price_pen=Decimal(precio), avg_price_pen=Decimal(precio), offers_count=2,
    )


def test_sin_nada_propio_que_decir_cae_a_la_frase_generica(client, peru_airports):
    """La mitad de las fechas baratas en cada tramo, rebaja normal y un solo
    destino: no hay momento, ni rigidez, ni puesto. Callar sería dejar la
    tabla sin explicación."""
    ida = _publicar("LIM", "PEM", mediana="200")
    vuelta = _publicar("PEM", "LIM", mediana="210")
    _caro(ida)
    _caro(vuelta)

    cuerpo = _pagina(client, "puerto-maldonado")
    assert "Ir y volver cuestan parecido" in cuerpo


def test_cinco_ciudades_parejas_publican_textos_distintos(client, peru_airports):
    """El defecto que se corrige: la misma frase en trece páginas."""
    Airport.objects.create(iata_code="JUL", name="Inca Manco Cápac", city="Juliaca", region="Puno")
    Airport.objects.create(iata_code="IQT", name="Secada", city="Iquitos", region="Loreto")
    medianas = {"CUZ": "220", "AQP": "250", "PEM": "280", "JUL": "310", "IQT": "340"}
    for destino, mediana in medianas.items():
        _publicar("LIM", destino, mediana=mediana)
        _publicar(destino, "LIM", mediana=mediana)

    slugs = {"CUZ": "cusco", "AQP": "arequipa", "PEM": "puerto-maldonado",
             "JUL": "juliaca", "IQT": "iquitos"}
    lecturas = []
    for slug in slugs.values():
        cuerpo = _pagina(client, slug)
        bloque = cuerpo.split('<div class="lectura')[1].split("</div>")[0]
        assert "Ir y volver cuestan parecido" not in bloque
        lecturas.append(bloque)
    assert len(set(lecturas)) == len(lecturas)
    assert "El viaje completo más barato desde Lima" in _pagina(client, "cusco")
    assert "El viaje completo más caro desde Lima" in _pagina(client, "iquitos")


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


def test_los_precios_del_viaje_no_se_parten_en_dos_lineas(client, peru_airports):
    """En 375 px la tabla dejaba «S/» en una línea y la cifra en la otra."""
    _publicar("LIM", "PEM", mediana="200")
    _publicar("PEM", "LIM", mediana="300")
    cuerpo = _pagina(client, "puerto-maldonado")
    tabla = cuerpo.split('<table class="compacta">')[1].split("</table>")[0]
    assert "S/&nbsp;200" in tabla and "S/&nbsp;300" in tabla and "S/&nbsp;500" in tabla
    assert "S/ 2" not in tabla and "S/ 3" not in tabla and "S/ 5" not in tabla


# --- lectura de viajes parejos (lógica pura) ------------------------------------

def _viaje(ida="250", vuelta="250"):
    return lectura.Viaje("Lima", "Talara", Decimal(ida), Decimal(vuelta))


def _tramo(mediana="250", minimo="150", baratas=10, fechas=40):
    return lectura.Tramo(Decimal(mediana), Decimal(minimo), baratas, fechas)


def _claves(obs):
    return [o.clave for o in obs]


def test_dice_que_tramo_cerrar_cuando_uno_tiene_mas_fechas_baratas():
    obs = lectura.leer_viaje_parejo(_viaje(), _tramo(baratas=20), _tramo(baratas=8), None)
    assert _claves(obs) == ["cerrar-ida"]
    assert obs[0].titular == "Hoy conviene cerrar primero la ida"
    obs = lectura.leer_viaje_parejo(_viaje(), _tramo(baratas=8), _tramo(baratas=20), None)
    assert _claves(obs) == ["cerrar-vuelta"]


def test_una_diferencia_chica_de_fechas_baratas_no_se_cuenta():
    # 25% contra 35%: 10 puntos, bajo el umbral de 15.
    obs = lectura.leer_viaje_parejo(_viaje(), _tramo(baratas=10), _tramo(baratas=14), None)
    assert obs == []


def test_los_dos_tramos_baratos_y_los_dos_caros():
    assert _claves(lectura.leer_viaje_parejo(
        _viaje(), _tramo(baratas=30), _tramo(baratas=31), None)) == ["ambos-baratos"]
    caros = lectura.leer_viaje_parejo(_viaje(), _tramo(baratas=0), _tramo(baratas=0), None)
    assert _claves(caros) == ["ambos-caros"]
    # «Solo 0 de 45» salió publicado una vez en las fichas: el cero tiene su frase.
    assert caros[0].detalle.startswith("Ninguna de las 40 fechas de ida")


def test_un_viaje_que_casi_no_baja_lo_dice():
    """Huánuco: el mínimo de 30 días quedó a 3% y 9% de la mediana."""
    obs = lectura.leer_viaje_parejo(
        _viaje(), _tramo(minimo="243", baratas=10), _tramo(minimo="230", baratas=12), None)
    assert _claves(obs) == ["rigido"]


def test_una_rebaja_grande_dice_que_tramo_la_dio():
    """Puerto Maldonado: la vuelta tocó un 62% bajo su mediana."""
    obs = lectura.leer_viaje_parejo(
        _viaje(), _tramo(minimo="150", baratas=10), _tramo(minimo="95", baratas=12), None)
    assert _claves(obs) == ["rebaja-vuelta"]
    assert "de Talara a Lima" in obs[0].detalle


@pytest.mark.parametrize("n,de,titular", [
    (1, 17, "El viaje completo más barato desde Lima"),
    (17, 17, "El viaje completo más caro desde Lima"),
    (3, 17, "El 3.º viaje completo más barato desde Lima"),
    (16, 17, "El 2.º viaje completo más caro desde Lima"),
])
def test_el_puesto_se_dice_desde_el_extremo_mas_cercano(n, de, titular):
    obs = lectura.leer_viaje_parejo(_viaje(), None, None, lectura.Puesto(n, de, "Lima"))
    assert obs[0].titular == titular


def test_el_puesto_concuerda_en_singular():
    obs = lectura.leer_viaje_parejo(_viaje(), None, None, lectura.Puesto(2, 17, "Lima"))
    assert "solo 1 destino sale más barato" in obs[0].detalle
    obs = lectura.leer_viaje_parejo(_viaje(), None, None, lectura.Puesto(16, 17, "Lima"))
    assert "solo 1 destino cuesta más" in obs[0].detalle


def test_sin_suficientes_destinos_no_hay_puesto(peru_airports):
    rutas = [_publicar("LIM", "CUZ"), _publicar("CUZ", "LIM"),
             _publicar("LIM", "AQP"), _publicar("AQP", "LIM")]
    assert lectura.puesto_del_viaje(rutas, "LIM", "CUZ") is None
    rutas += [_publicar("LIM", "PEM", mediana="900"), _publicar("PEM", "LIM", mediana="900")]
    puesto = lectura.puesto_del_viaje(rutas, "LIM", "PEM")
    assert (puesto.n, puesto.de, puesto.origen) == (3, 3, "Lima")
