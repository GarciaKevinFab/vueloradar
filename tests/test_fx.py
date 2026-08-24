"""Tipo de cambio USD→PEN: cache, fallback y sanidad del valor. Sin red."""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.scraping import fx


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def test_usa_la_primera_fuente_que_responde():
    with patch.object(fx, "_get_json", return_value={"rates": {"PEN": 3.75}}) as get_json:
        assert fx.usd_to_pen() == Decimal("3.7500")

    assert get_json.call_count == 1


def test_cachea_el_resultado():
    with patch.object(fx, "_get_json", return_value={"rates": {"PEN": 3.75}}) as get_json:
        fx.usd_to_pen()
        fx.usd_to_pen()

    assert get_json.call_count == 1, "la segunda llamada debe salir del cache"


def test_force_refresh_ignora_el_cache():
    with patch.object(fx, "_get_json", return_value={"rates": {"PEN": 3.75}}) as get_json:
        fx.usd_to_pen()
        fx.usd_to_pen(force_refresh=True)

    assert get_json.call_count == 2


def test_cae_a_la_segunda_fuente_si_la_primera_falla():
    responses = [OSError("timeout"), {"usd": {"pen": 3.90}}]

    def fake_get_json(url):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch.object(fx, "_get_json", side_effect=fake_get_json):
        assert fx.usd_to_pen() == Decimal("3.9000")


def test_fallback_del_env_si_todas_las_fuentes_fallan(settings):
    settings.FX_FALLBACK_USD_PEN = "3.80"

    with patch.object(fx, "_get_json", side_effect=OSError("sin red")):
        assert fx.usd_to_pen() == Decimal("3.8000")


def test_descarta_tasas_fuera_de_rango(settings):
    """Una tasa de 0.29 significa que la fuente devolvió PEN→USD, no USD→PEN."""
    settings.FX_FALLBACK_USD_PEN = "3.80"

    with patch.object(fx, "_get_json", return_value={"rates": {"PEN": 0.29}}):
        assert fx.usd_to_pen() == Decimal("3.8000")


def test_respuesta_con_forma_inesperada_no_rompe(settings):
    settings.FX_FALLBACK_USD_PEN = "3.80"

    with patch.object(fx, "_get_json", return_value={"algo": "distinto"}):
        assert fx.usd_to_pen() == Decimal("3.8000")


def test_redis_caido_no_rompe_la_conversion():
    with patch.object(fx.cache, "get", side_effect=ConnectionError("redis caído")), patch.object(
        fx.cache, "set", side_effect=ConnectionError("redis caído")
    ), patch.object(fx, "_get_json", return_value={"rates": {"PEN": 3.75}}):
        assert fx.usd_to_pen() == Decimal("3.7500")


def test_convert_to_pen():
    with patch.object(fx, "_get_json", return_value={"rates": {"PEN": 3.50}}):
        assert fx.convert_to_pen(Decimal("120"), "USD") == Decimal("420.00")

    assert fx.convert_to_pen(Decimal("450"), "PEN") == Decimal("450.00")
    assert fx.convert_to_pen(Decimal("450"), "") == Decimal("450.00")


def test_moneda_no_soportada_se_asume_pen():
    """Mejor un precio sin convertir que reventar la búsqueda entera."""
    assert fx.convert_to_pen(Decimal("100"), "CLP") == Decimal("100.00")
