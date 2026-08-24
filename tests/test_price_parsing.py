"""Normalización de precios: lo que devuelven las fuentes → Decimal en PEN."""

from decimal import Decimal

import pytest

from apps.scraping.providers.base import CURRENCY_TOKENS, PriceParseError, parse_price


@pytest.mark.parametrize(
    ("raw", "amount", "currency"),
    [
        ("$120", Decimal("120.00"), "USD"),
        ("S/ 450", Decimal("450.00"), "PEN"),
        ("PEN 1,203.50", Decimal("1203.50"), "PEN"),
        ("S/. 89.90", Decimal("89.90"), "PEN"),
        ("US$ 1,050", Decimal("1050.00"), "USD"),
        ("USD 99", Decimal("99.00"), "USD"),
        # Formato europeo: el punto es separador de miles.
        ("1.203,50 €", Decimal("1203.50"), "EUR"),
        # Espacio duro (nbsp), tal como llega de Google.
        ("PEN\xa0202", Decimal("202.00"), "PEN"),
        # Miles sin decimales: la coma NO es decimal.
        ("S/ 1,203", Decimal("1203.00"), "PEN"),
    ],
)
def test_parsea_precios_de_las_fuentes(raw, amount, currency):
    assert parse_price(raw) == (amount, currency)


def test_numero_pelado_asume_la_moneda_por_defecto():
    assert parse_price(202) == (Decimal("202.00"), "PEN")
    assert parse_price(202, default_currency="USD") == (Decimal("202.00"), "USD")


def test_string_sin_moneda_asume_la_moneda_por_defecto():
    assert parse_price("450.00") == (Decimal("450.00"), "PEN")


@pytest.mark.parametrize("raw", ["", "   ", "Ver precio", "Check price"])
def test_precio_sin_importe_falla_explicitamente(raw):
    with pytest.raises(PriceParseError):
        parse_price(raw)


def test_us_dollar_gana_sobre_el_simbolo_suelto():
    """'US$' debe resolverse antes que '$' para no confundir monedas."""
    assert CURRENCY_TOKENS["US$"] == "USD"
    assert parse_price("US$ 50")[1] == "USD"
