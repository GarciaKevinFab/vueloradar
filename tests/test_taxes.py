"""Normalización de tarifa base a precio final, y su aplicación en providers."""

from datetime import date
from decimal import Decimal

import pytest

from apps.scraping.providers.base import RawFlightOffer
from apps.scraping.providers.playwright_base import DirectScraperProvider
from apps.scraping.taxes import base_fare_to_final

FECHA = date(2026, 9, 6)


def _oferta(precio):
    return RawFlightOffer(
        origin="LIM", destination="CUZ", search_date=FECHA,
        price_pen=Decimal(precio), source="fake", airline="Fake",
    )


class _ConTarifaBase(DirectScraperProvider):
    source_name = "fake_base"
    publishes_base_fare = True

    def _search(self, origin, dest, date):
        return [_oferta("144.52")]


class _ConPrecioFinal(DirectScraperProvider):
    source_name = "fake_final"

    def _search(self, origin, dest, date):
        return [_oferta("201.00")]


# --- la fórmula -------------------------------------------------------------

def test_reproduce_la_observacion_verificada():
    """JetSMART LIM-CUZ 06/09, medido el 2026-08-23 contra Google Flights.

    144,52 x 1,18 = 170,53 + 30,47 de TUUA = 201,00. Si este test se rompe,
    o cambió la TUUA o alguien invirtió el orden de los impuestos.
    """
    assert base_fare_to_final(Decimal("144.52")) == Decimal("201.00")


def test_el_igv_no_grava_la_tuua():
    """Invertir el orden daría 206,48: la TUUA se suma después del IGV."""
    assert base_fare_to_final(Decimal("144.52")) != (
        (Decimal("144.52") + Decimal("30.47")) * Decimal("1.18")
    ).quantize(Decimal("0.01"))


def test_redondea_a_centimos():
    assert base_fare_to_final(Decimal("100")).as_tuple().exponent == -2


def test_tarifa_cero_es_solo_la_tuua(settings):
    assert base_fare_to_final(Decimal("0")) == Decimal(settings.TUUA_NACIONAL_PEN)


def test_tarifa_negativa_revienta():
    """Un precio negativo es un error de parseo; dejarlo pasar contamina el histórico."""
    with pytest.raises(ValueError):
        base_fare_to_final(Decimal("-1"))


def test_la_tuua_sale_de_settings(settings):
    settings.TUUA_NACIONAL_PEN = Decimal("40.00")
    assert base_fare_to_final(Decimal("100")) == Decimal("158.00")


# --- aplicación en los providers -------------------------------------------

def test_provider_de_tarifa_base_devuelve_precio_final():
    """Regresión: Sky parecía 25-30% más barato y por eso la verificación
    estaba apagada. Ahora sale normalizado de `search()`."""
    ofertas = _ConTarifaBase().search("LIM", "CUZ", FECHA)
    assert ofertas[0].price_pen == Decimal("201.00")


def test_provider_de_precio_final_no_se_toca():
    ofertas = _ConPrecioFinal().search("LIM", "CUZ", FECHA)
    assert ofertas[0].price_pen == Decimal("201.00")


def test_sky_esta_declarado_como_tarifa_base():
    from apps.scraping.providers.sky import SkyProvider

    assert SkyProvider.publishes_base_fare is True


def test_google_flights_no_es_tarifa_base():
    """Google ya entrega precio final: normalizarlo lo inflaría dos veces."""
    from apps.scraping.providers.google_flights import GoogleFlightsProvider

    assert GoogleFlightsProvider.publishes_base_fare is False


def test_jetsmart_es_tarifa_base():
    """Corregido tras verificar en vivo el 2026-08-27.

    La nota del proyecto decía que el calendario incluía impuestos. No: trae el
    enlace "Ver precios con tasas e impuestos" y, aplicando IGV + TUUA, encaja
    con nuestros datos de Google Flights. Sin este flag sus precios entrarían
    ~30% bajos y dispararían alertas falsas.
    """
    from apps.scraping.providers.jetsmart import JetSmartProvider

    assert JetSmartProvider.publishes_base_fare is True
