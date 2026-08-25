"""Ida y vuelta: parseo del pedido, suma de tramos y precio de venta."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache

from apps.ai_analyst.nl_parser import parse_flight_request
from bot import formatting

LIMA = ZoneInfo("America/Lima")
HOY = date(2026, 8, 25)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def claude_dice(**campos):
    base = {
        "is_flight_search": True, "origin_iata": None, "dest_iata": None,
        "date": None, "return_date": None, "flexible_days": 0,
    }
    base.update(campos)
    return patch(
        "apps.ai_analyst.nl_parser.complete_json", return_value=(base, "anthropic")
    )


# ------------------------------------------------------------------ parseo
@pytest.mark.django_db
def test_entiende_el_viaje_escrito_como_lo_dice_la_gente(peru_airports):
    """El mensaje real que originó esta función."""
    with claude_dice(origin_iata="PEM", dest_iata="LIM",
                     date="2026-10-16", return_date="2026-10-18"):
        i = parse_flight_request(
            "Pasaje para el 16 de octubre iba d dpto a Lima y de lima puerto el 18",
            today=HOY,
        )

    assert i.origin == "PEM" and i.dest == "LIM"
    assert i.date == date(2026, 10, 16)
    assert i.return_date == date(2026, 10, 18)
    assert i.is_round_trip is True


@pytest.mark.django_db
def test_solo_ida_no_es_ida_y_vuelta(peru_airports):
    with claude_dice(origin_iata="LIM", dest_iata="CUZ", date="2026-10-16"):
        i = parse_flight_request("de lima a cusco el 16 de octubre", today=HOY)

    assert i.return_date is None
    assert i.is_round_trip is False


@pytest.mark.django_db
def test_vuelta_anterior_a_la_ida_se_descarta(peru_airports):
    """Es un error de lectura del modelo, no un viaje posible."""
    with claude_dice(origin_iata="PEM", dest_iata="LIM",
                     date="2026-10-18", return_date="2026-10-16"):
        i = parse_flight_request("...", today=HOY)

    assert i.return_date is None
    assert i.is_round_trip is False


@pytest.mark.django_db
def test_vuelta_el_mismo_dia_se_descarta(peru_airports):
    with claude_dice(origin_iata="PEM", dest_iata="LIM",
                     date="2026-10-16", return_date="2026-10-16"):
        assert parse_flight_request("...", today=HOY).is_round_trip is False


@pytest.mark.django_db
def test_la_ida_y_vuelta_sobrevive_al_cache(peru_airports):
    with claude_dice(origin_iata="PEM", dest_iata="LIM",
                     date="2026-10-16", return_date="2026-10-18") as ia:
        primera = parse_flight_request("de puerto a lima el 16 y vuelvo el 18", today=HOY)
        segunda = parse_flight_request("De Puerto a Lima el 16 y vuelvo el 18", today=HOY)

    assert ia.call_count == 1
    assert primera == segunda
    assert segunda.return_date == date(2026, 10, 18)


# ------------------------------------------------------------------ formato
class FakeOffer:
    def __init__(self, price, airline="LATAM", hour=12, stops=0):
        self.price_pen = Decimal(price)
        self.airline = airline
        self.stops = stops
        self.pk = 1
        self.departure_dt = datetime(2026, 10, 16, hour, 0, tzinfo=LIMA)
        self.arrival_dt = self.departure_dt + timedelta(hours=1, minutes=35)


IDA = [FakeOffer("531", stops=1, hour=16), FakeOffer("674", hour=12)]
VUELTA = [FakeOffer("442", stops=1, hour=6), FakeOffer("521", hour=12)]


def test_suma_los_dos_tramos():
    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA,
    )

    assert "PEM ⇄ LIM" in t
    assert "S/ 531" in t and "S/ 442" in t
    assert "Total del viaje: S/ 973" in t, "531 + 442"
    assert "Todo directo: S/ 1,195" in t, "674 + 521"


def test_sin_margen_por_defecto():
    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA,
    )
    assert "Ganancia" not in t and "Tu venta" not in t


def test_con_margen_muestra_el_desglose(settings):
    settings.SALE_MARKUP_PCT = Decimal("10")
    settings.SALE_MARKUP_MIN_PEN = Decimal("25")
    settings.SALE_ROUND_TO_PEN = Decimal("5")

    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA, show_sale=True,
    )

    assert "Costo:" in t and "S/ 973" in t
    assert "Tu venta" in t and "S/ 1,075" in t
    assert "Ganancia" in t and "S/ 102" in t


def test_avisa_que_el_paquete_suele_salir_menos():
    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA,
    )
    assert "paquete" in t and "techo" in t


def test_un_tramo_sin_vuelos_no_inventa_un_total():
    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=[], inbound=VUELTA,
    )

    assert "Total del viaje" not in t
    assert "No encontré vuelos" in t and "la ida" in t
    assert "S/ 442" in t, "igual dice cuánto salía el tramo que sí encontró"
