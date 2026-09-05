"""Buscador de la web: parser determinista y link de compra."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.web.search import Consulta, booking_url, parse_consulta


@pytest.fixture
def aeropuertos(peru_airports):
    from apps.flights.models import Airport

    return list(Airport.objects.all())


# --- lo que la gente escribe de verdad --------------------------------------

@pytest.mark.parametrize("frase,origen,destino", [
    ("de lima a cusco el 15 de setiembre", "LIM", "CUZ"),
    ("Lima Cusco 15/09", "LIM", "CUZ"),
    ("LIM CUZ 2026-09-15", "LIM", "CUZ"),
    ("quiero ir de Arequipa a Lima", "AQP", "LIM"),
    ("vuelo a Puerto Maldonado desde Cusco", "CUZ", "PEM"),
])
def test_entiende_las_formas_habituales(aeropuertos, frase, origen, destino):
    c = parse_consulta(frase, aeropuertos)
    assert (c.origen, c.destino) == (origen, destino)


def test_desde_invierte_el_sentido(aeropuertos):
    """Regresión: «a Cusco desde Lima» se leía como si saliera de Cusco."""
    c = parse_consulta("vuelos a cusco desde lima", aeropuertos)
    assert (c.origen, c.destino) == ("LIM", "CUZ")


def test_de_no_se_confunde_con_la_fecha(aeropuertos):
    """El «de» de «15 de setiembre» no debe alterar el orden de las ciudades."""
    c = parse_consulta("lima a cusco el 15 de setiembre", aeropuertos)
    assert (c.origen, c.destino) == ("LIM", "CUZ")


def test_ciudad_sin_aeropuerto_propio(aeropuertos):
    """Huancayo no tiene aeropuerto: su vuelo sale de Jauja."""
    from apps.flights.models import Airport

    Airport.objects.create(iata_code="JAU", name="Francisco Carle", city="Jauja", region="Junín")
    c = parse_consulta("de huancayo a lima", list(Airport.objects.all()))
    assert c.origen == "JAU"


def test_texto_incomprensible_no_inventa(aeropuertos):
    c = parse_consulta("hola qué tal", aeropuertos)
    assert c == Consulta()
    assert c.es_completa is False


def test_texto_vacio(aeropuertos):
    assert parse_consulta("", aeropuertos).es_completa is False


# --- fechas -----------------------------------------------------------------

def test_relativas(aeropuertos):
    hoy = timezone.localdate()
    assert parse_consulta("lima a cusco hoy", aeropuertos).fecha == hoy
    assert parse_consulta("lima a cusco mañana", aeropuertos).fecha == hoy + timedelta(days=1)
    assert parse_consulta("lima a cusco pasado mañana", aeropuertos).fecha == hoy + timedelta(days=2)


def test_sin_anio_se_asume_la_proxima(aeropuertos):
    """Quien escribe «15 de enero» en agosto quiere el enero que viene."""
    c = parse_consulta("lima a cusco el 15 de enero", aeropuertos)
    assert c.fecha >= timezone.localdate()


def test_fecha_imposible_no_revienta(aeropuertos):
    assert parse_consulta("lima a cusco el 31 de febrero", aeropuertos).fecha is None


# --- link de compra ---------------------------------------------------------

def test_el_link_cambia_con_los_pasajeros():
    """El conteo va dentro del blob `tfs`: no se puede pegar como parámetro."""
    solo = booking_url("LIM", "CUZ", date(2026, 9, 15), adultos=1, ninos=0)
    familia = booking_url("LIM", "CUZ", date(2026, 9, 15), adultos=2, ninos=1)
    assert solo != familia
    assert solo.startswith("https://www.google.com/travel/flights")


def test_el_link_nunca_lleva_cero_adultos():
    """Un viaje sin adultos no existe; Google rechazaría la query."""
    assert booking_url("LIM", "CUZ", date(2026, 9, 15), adultos=0, ninos=2) == booking_url(
        "LIM", "CUZ", date(2026, 9, 15), adultos=1, ninos=2
    )


# --- la pagina --------------------------------------------------------------

def test_el_buscador_vacio_responde(client, db):
    resp = client.get("/buscar/")
    assert resp.status_code == 200
    assert "quieres ir" in resp.content.decode()


def test_una_busqueda_completa_da_link_de_compra(client, peru_airports):
    resp = client.get("/buscar/", {"q": "de lima a cusco el 15 de setiembre", "adultos": 2, "ninos": 1})
    cuerpo = resp.content.decode()
    assert resp.status_code == 200
    assert "google.com/travel/flights" in cuerpo
    assert "no multiplicamos nuestro precio" in cuerpo


def test_los_resultados_no_se_indexan(client, peru_airports):
    """Cada combinación de texto sería una URL distinta: basura para el índice."""
    cuerpo = client.get("/buscar/", {"q": "lima a cusco mañana"}).content.decode()
    assert 'name="robots" content="noindex,follow"' in cuerpo
    assert 'noindex' not in client.get("/buscar/").content.decode()


def test_texto_incompleto_explica_que_falta(client, peru_airports):
    cuerpo = client.get("/buscar/", {"q": "quiero viajar"}).content.decode()
    assert "No entendimos del todo" in cuerpo


def test_los_pasajeros_se_acotan(client, peru_airports):
    """Un valor absurdo en la query string no puede llegar al constructor."""
    cuerpo = client.get("/buscar/", {"q": "lima a cusco mañana", "adultos": 999, "ninos": -5}).content.decode()
    assert "9 adultos" in cuerpo
