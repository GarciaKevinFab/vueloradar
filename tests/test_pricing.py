"""Precio de venta: margen del operador sobre el costo del pasaje."""

from decimal import Decimal

import pytest

from apps.flights.pricing import quote


@pytest.fixture(autouse=True)
def margen(settings):
    settings.SALE_MARKUP_PCT = Decimal("10")
    settings.SALE_MARKUP_MIN_PEN = Decimal("25")
    settings.SALE_ROUND_TO_PEN = Decimal("5")


def test_pasaje_caro_usa_el_porcentaje():
    q = quote(Decimal("1000"))
    assert q.cost == Decimal("1000.00")
    assert q.sale == Decimal("1100"), "1000 + 10% = 1100, ya múltiplo de 5"
    assert q.margin == Decimal("100.00")


def test_pasaje_barato_usa_el_piso_fijo():
    """Un 10% sobre S/ 150 son S/ 15: no paga el trabajo de gestionar la compra."""
    q = quote(Decimal("150"))
    assert q.margin >= Decimal("25"), "manda el piso, no el porcentaje"
    assert q.sale == Decimal("175")


def test_el_precio_final_se_redondea_hacia_arriba():
    q = quote(Decimal("442"))
    assert q.sale % Decimal("5") == 0, "nadie cotiza S/ 486,20"
    assert q.sale == Decimal("490")
    assert q.margin == Decimal("48.00")


def test_el_margen_reportado_es_el_real_tras_redondear():
    q = quote(Decimal("973"))
    assert q.sale == Decimal("1075")
    assert q.margin == q.sale - q.cost
    assert q.margin_pct == Decimal("10.5")


def test_costo_cero_no_inventa_margen():
    q = quote(Decimal("0"))
    assert q.sale == Decimal("0.00") and q.margin == Decimal("0.00")


def test_sin_redondeo_configurado(settings):
    settings.SALE_ROUND_TO_PEN = Decimal("0")
    q = quote(Decimal("442"))
    assert q.sale == Decimal("486.20")


def test_porcentaje_configurable(settings):
    settings.SALE_MARKUP_PCT = Decimal("20")
    settings.SALE_MARKUP_MIN_PEN = Decimal("0")
    settings.SALE_ROUND_TO_PEN = Decimal("0")
    assert quote(Decimal("500")).sale == Decimal("600.00")
