"""Tipo de cambio USD→PEN: cache, fallback y sanidad del valor. Sin red."""

import json
from datetime import timedelta
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


def test_sin_fuentes_ni_tasa_previa_no_se_inventa_una():
    """Antes había una tasa fija en el .env que se usaba en silencio.

    Guardar un precio calculado con un número inventado contamina el histórico
    y nadie se entera. Ahora la conversión falla y el caller descarta la oferta.
    """
    with patch.object(fx, "_get_json", side_effect=OSError("sin red")), patch.object(
        fx, "_avisar_al_admin"
    ) as aviso:
        with pytest.raises(fx.RateUnavailable):
            fx.usd_to_pen()
    aviso.assert_called_once()


def test_sin_fuentes_se_usa_la_ultima_tasa_buena():
    """El respaldo es la última tasa real observada, no una constante."""
    with patch.object(fx, "_get_json", return_value={"rates": {"PEN": 3.71}}):
        assert fx.usd_to_pen(force_refresh=True) == Decimal("3.7100")

    # Se vacía el cache normal pero no el de última buena.
    fx.cache.delete(fx.CACHE_KEY)
    with patch.object(fx, "_get_json", side_effect=OSError("sin red")):
        assert fx.usd_to_pen() == Decimal("3.7100")


def test_la_ultima_tasa_buena_caduca(settings):
    """Pasado el límite deja de servir: una tasa de la semana pasada es ficción."""
    from django.utils import timezone

    settings.FX_LAST_GOOD_MAX_AGE_HOURS = 24
    viejo = (timezone.now() - timedelta(hours=30)).isoformat()
    fx.cache.set(fx.LAST_GOOD_KEY, json.dumps({"rate": "3.7100", "at": viejo}))

    with patch.object(fx, "_get_json", side_effect=OSError("sin red")), patch.object(
        fx, "_avisar_al_admin"
    ):
        with pytest.raises(fx.RateUnavailable):
            fx.usd_to_pen()


def test_descarta_tasas_fuera_de_rango():
    """Una tasa de 0.29 significa que la fuente devolvió PEN→USD, no USD→PEN."""
    with patch.object(fx, "_get_json", return_value={"rates": {"PEN": 0.29}}), patch.object(
        fx, "_avisar_al_admin"
    ):
        with pytest.raises(fx.RateUnavailable):
            fx.usd_to_pen()


def test_respuesta_con_forma_inesperada_no_rompe():
    with patch.object(fx, "_get_json", return_value={"algo": "distinto"}), patch.object(
        fx, "_avisar_al_admin"
    ):
        with pytest.raises(fx.RateUnavailable):
            fx.usd_to_pen()


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


def test_moneda_no_soportada_se_descarta():
    """Asumir PEN para un monto en otra moneda inventa un precio.

    Con CLP, 100 no son 100 soles: guardarlo así mete basura en el histórico.
    """
    assert fx.convert_to_pen(Decimal("100"), "CLP") is None


def test_sin_tipo_de_cambio_la_oferta_en_dolares_se_descarta():
    with patch.object(fx, "_get_json", side_effect=OSError("sin red")), patch.object(
        fx, "_avisar_al_admin"
    ):
        assert fx.convert_to_pen(Decimal("50"), "USD") is None
