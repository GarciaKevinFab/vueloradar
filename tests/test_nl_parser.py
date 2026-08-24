"""Parser de lenguaje natural. La respuesta de Claude se mockea: cero red."""

from datetime import date
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.ai_analyst.nl_parser import parse_flight_request

HOY = date(2026, 9, 1)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def claude_dice(**campos):
    """Respuesta cruda de Claude, con los defaults del schema."""
    base = {
        "is_flight_search": True,
        "origin_iata": None,
        "dest_iata": None,
        "date": None,
        "flexible_days": 0,
    }
    base.update(campos)
    # El router devuelve (datos, proveedor).
    return patch("apps.ai_analyst.nl_parser.complete_json", return_value=(base, "anthropic"))


@pytest.mark.django_db
def test_mapea_ciudades_a_iata(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="PEM", date="2026-09-20"):
        intent = parse_flight_request("vuelo de lima a puerto maldonado el 20 de setiembre", today=HOY)

    assert intent.is_flight_search is True
    assert intent.origin == "LIM"
    assert intent.dest == "PEM"
    assert intent.date == date(2026, 9, 20)
    assert intent.flexible_days == 0
    assert intent.is_complete is True


@pytest.mark.django_db
def test_normaliza_iata_en_minusculas(peru_airports):
    with claude_dice(origin_iata="lim", dest_iata=" cuz ", date="2026-09-15"):
        intent = parse_flight_request("de lima a cusco", today=HOY)

    assert (intent.origin, intent.dest) == ("LIM", "CUZ")


@pytest.mark.django_db
def test_descarta_iata_que_no_existe_en_la_base(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="XYZ", date="2026-09-15"):
        intent = parse_flight_request("de lima a narnia", today=HOY)

    assert intent.origin == "LIM"
    assert intent.dest is None
    assert intent.is_complete is False
    assert intent.missing == ["el destino"]


@pytest.mark.django_db
def test_origen_igual_a_destino_se_descarta(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="LIM", date="2026-09-15"):
        intent = parse_flight_request("de lima a lima", today=HOY)

    assert intent.origin is None and intent.dest is None


@pytest.mark.django_db
def test_fecha_pasada_se_descarta(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2026-08-01"):
        intent = parse_flight_request("de lima a cusco el 1 de agosto", today=HOY)

    assert intent.date is None
    assert intent.is_complete is False


@pytest.mark.django_db
def test_fecha_demasiado_lejana_se_descarta(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2030-01-01"):
        intent = parse_flight_request("de lima a cusco en 2030", today=HOY)

    assert intent.date is None


@pytest.mark.django_db
def test_fecha_malformada_se_descarta(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="15 de setiembre"):
        intent = parse_flight_request("de lima a cusco", today=HOY)

    assert intent.date is None


@pytest.mark.django_db
def test_flexibilidad_se_topea_en_tres_dias(peru_airports, settings):
    settings.BOT_MAX_FLEXIBLE_DAYS = 3
    with claude_dice(origin_iata="CUZ", dest_iata="AQP", date="2026-09-08", flexible_days=7):
        intent = parse_flight_request("de cusco a arequipa la próxima semana", today=HOY)

    assert intent.flexible_days == 3


@pytest.mark.django_db
def test_flexibilidad_negativa_o_basura_cae_a_cero(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2026-09-15", flexible_days=-2):
        assert parse_flight_request("x", today=HOY).flexible_days == 0

    cache.clear()
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2026-09-15", flexible_days="mucho"):
        assert parse_flight_request("y", today=HOY).flexible_days == 0


@pytest.mark.django_db
def test_mensaje_que_no_pide_vuelo(peru_airports):
    with claude_dice(is_flight_search=False):
        intent = parse_flight_request("hola qué tal", today=HOY)

    assert intent.is_flight_search is False
    assert intent.is_complete is False


@pytest.mark.django_db
def test_mensaje_vacio_no_llama_a_la_ia(peru_airports):
    with patch("apps.ai_analyst.nl_parser.complete_json") as ia:
        intent = parse_flight_request("   ", today=HOY)

    ia.assert_not_called()
    assert intent.is_flight_search is False


@pytest.mark.django_db
def test_si_ningun_proveedor_responde_el_bot_no_se_rompe(peru_airports):
    """El router devuelve None cuando toda la cadena falló."""
    with patch("apps.ai_analyst.nl_parser.complete_json", return_value=None):
        intent = parse_flight_request("de lima a cusco mañana", today=HOY)

    assert intent.is_flight_search is False


@pytest.mark.django_db
def test_la_segunda_consulta_identica_sale_del_cache(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2026-09-15") as ia:
        primera = parse_flight_request("de lima a cusco el 15", today=HOY)
        segunda = parse_flight_request("De Lima a Cusco el 15", today=HOY)

    assert ia.call_count == 1, "el cache debe evitar la segunda llamada"
    assert primera == segunda


@pytest.mark.django_db
def test_el_cache_no_cruza_dias(peru_airports):
    """'mañana' no significa lo mismo hoy que ayer."""
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2026-09-02") as ia:
        parse_flight_request("de lima a cusco mañana", today=HOY)
        parse_flight_request("de lima a cusco mañana", today=date(2026, 9, 2))

    assert ia.call_count == 2


@pytest.mark.django_db
def test_sin_aeropuertos_cargados_no_llama_a_la_ia(db):
    with patch("apps.ai_analyst.nl_parser.complete_json") as ia:
        intent = parse_flight_request("de lima a cusco", today=HOY)

    ia.assert_not_called()
    assert intent.is_flight_search is False


@pytest.mark.django_db
def test_el_prompt_lleva_la_tabla_de_aeropuertos_de_la_base(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2026-09-15") as ia:
        parse_flight_request("de lima a cusco el 15", today=HOY)

    system = ia.call_args.args[0]
    assert "LIM: Lima" in system
    assert "PEM: Puerto Maldonado" in system
    assert "2026-09-01" in system
    assert "martes" in system
