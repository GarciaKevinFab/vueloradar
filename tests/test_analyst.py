"""Analista de compra: normalización del veredicto y degradación sin IA."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.ai_analyst.analyst import Verdict, get_verdict

FECHA_VUELO = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def route(peru_airports):
    from apps.flights.models import Route

    return Route.objects.create(origin_id="LIM", destination_id="PEM", is_monitored=True)


@pytest.fixture
def con_historico(route):
    from apps.flights.models import PriceSnapshot, RouteStats

    RouteStats.objects.create(
        route=route, avg_30d=Decimal("221"), min_30d=Decimal("168"),
        p25_30d=Decimal("190"), median_30d=Decimal("215"), samples_count=30,
    )
    for precio in ("240", "225", "210", "195"):
        PriceSnapshot.objects.create(
            route=route, flight_date=FECHA_VUELO,
            min_price_pen=Decimal(precio), avg_price_pen=Decimal(precio) + 30,
            offers_count=6, cheapest_airline="Sky",
        )
    return route


def ia_responde(**campos):
    base = {"action": "comprar", "confidence": 80, "reason": "Está bajo el p25 de 30 días."}
    base.update(campos)
    return patch("apps.ai_analyst.analyst.complete_json", return_value=(base, "anthropic"))


# ------------------------------------------------------------------ veredicto
@pytest.mark.django_db
def test_veredicto_completo(con_historico):
    with ia_responde():
        verdict = get_verdict(con_historico, FECHA_VUELO, Decimal("152"))

    assert verdict.action == "comprar"
    assert verdict.is_buy is True
    assert verdict.label == "COMPRA"
    assert verdict.confidence == 80
    assert verdict.provider == "anthropic"


@pytest.mark.django_db
def test_veredicto_de_esperar(con_historico):
    with ia_responde(action="esperar", confidence=60, reason="La tendencia baja."):
        verdict = get_verdict(con_historico, FECHA_VUELO, Decimal("300"))

    assert verdict.is_buy is False
    assert verdict.label == "ESPERA"


@pytest.mark.django_db
def test_acepta_la_accion_en_ingles(con_historico):
    """Los modelos de respaldo a veces contestan buy/wait."""
    with ia_responde(action="buy"):
        assert get_verdict(con_historico, FECHA_VUELO, Decimal("152")).action == "comprar"


@pytest.mark.django_db
def test_accion_irreconocible_se_descarta(con_historico):
    with ia_responde(action="quizás"):
        assert get_verdict(con_historico, FECHA_VUELO, Decimal("152")) is None


@pytest.mark.django_db
def test_veredicto_sin_razon_se_descarta(con_historico):
    """Un 'COMPRA' sin justificación no le sirve a nadie."""
    with ia_responde(reason=""):
        assert get_verdict(con_historico, FECHA_VUELO, Decimal("152")) is None


@pytest.mark.django_db
def test_confianza_se_acota_a_0_100(con_historico):
    with ia_responde(confidence=180):
        assert get_verdict(con_historico, FECHA_VUELO, Decimal("152")).confidence == 100

    cache.clear()
    with ia_responde(confidence=-5):
        assert get_verdict(con_historico, FECHA_VUELO, Decimal("152")).confidence == 0

    cache.clear()
    with ia_responde(confidence="mucha"):
        assert get_verdict(con_historico, FECHA_VUELO, Decimal("152")).confidence == 0


# ------------------------------------------------------------- degradación
@pytest.mark.django_db
def test_sin_historico_no_hay_veredicto(route, settings):
    settings.VERDICT_MIN_SAMPLES = 10
    with patch("apps.ai_analyst.analyst.complete_json") as ia:
        assert get_verdict(route, FECHA_VUELO, Decimal("152")) is None

    ia.assert_not_called(), "sin stats no se gasta una llamada a la IA"


@pytest.mark.django_db
def test_con_pocas_muestras_no_hay_veredicto(route, settings):
    from apps.flights.models import RouteStats

    settings.VERDICT_MIN_SAMPLES = 10
    RouteStats.objects.create(
        route=route, avg_30d=Decimal("221"), min_30d=Decimal("168"),
        p25_30d=Decimal("190"), median_30d=Decimal("215"), samples_count=5,
    )
    with patch("apps.ai_analyst.analyst.complete_json") as ia:
        assert get_verdict(route, FECHA_VUELO, Decimal("152")) is None

    ia.assert_not_called()


@pytest.mark.django_db
def test_si_ningun_proveedor_responde_devuelve_none(con_historico):
    with patch("apps.ai_analyst.analyst.complete_json", return_value=None):
        assert get_verdict(con_historico, FECHA_VUELO, Decimal("152")) is None


# ------------------------------------------------------------------- cache
@pytest.mark.django_db
def test_precios_de_la_misma_banda_comparten_veredicto(con_historico, settings):
    settings.VERDICT_PRICE_BAND = Decimal("10")

    with ia_responde() as ia:
        get_verdict(con_historico, FECHA_VUELO, Decimal("152"))
        get_verdict(con_historico, FECHA_VUELO, Decimal("158"))

    assert ia.call_count == 1, "152 y 158 caen en la misma banda de S/ 10"


@pytest.mark.django_db
def test_bandas_distintas_piden_veredicto_nuevo(con_historico, settings):
    settings.VERDICT_PRICE_BAND = Decimal("10")

    with ia_responde() as ia:
        get_verdict(con_historico, FECHA_VUELO, Decimal("152"))
        get_verdict(con_historico, FECHA_VUELO, Decimal("195"))

    assert ia.call_count == 2


# ------------------------------------------------------------------ contexto
@pytest.mark.django_db
def test_el_contexto_lleva_los_numeros_de_la_base(con_historico):
    with ia_responde() as ia:
        get_verdict(con_historico, FECHA_VUELO, Decimal("152"))

    contexto = ia.call_args.args[1]
    assert "LIM" in contexto and "PEM" in contexto
    assert "221" in contexto, "el promedio de 30d"
    assert "190" in contexto, "el p25"
    assert "152" in contexto, "el precio actual"
    assert "S/ 240.00" in contexto, "el histórico de snapshots"
    assert "miércoles" in contexto, "el día de semana del vuelo"
