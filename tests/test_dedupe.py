"""Deduplicación de ofertas: el mismo vuelo físico sale una sola vez."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.scraping.providers.base import RawFlightOffer
from apps.scraping.services import dedupe_offers

LIMA = ZoneInfo("America/Lima")
SEARCH_DATE = date(2026, 9, 15)


def offer(
    *,
    airline="LATAM",
    flight_number="LA2034",
    hour=8,
    price="450.00",
    source="google_flights",
    origin="LIM",
    destination="CUZ",
):
    return RawFlightOffer(
        origin=origin,
        destination=destination,
        search_date=SEARCH_DATE,
        price_pen=Decimal(price),
        source=source,
        airline=airline,
        flight_number=flight_number,
        departure_dt=datetime(2026, 9, 15, hour, 0, tzinfo=LIMA),
        arrival_dt=datetime(2026, 9, 15, hour + 1, 20, tzinfo=LIMA),
        stops=0,
    )


def test_mismo_vuelo_en_dos_fuentes_se_queda_con_el_mas_barato():
    result = dedupe_offers(
        [
            offer(price="450.00", source="google_flights"),
            offer(price="399.00", source="sky"),
        ]
    )

    assert len(result) == 1
    assert result[0].price_pen == Decimal("399.00")
    assert result[0].source == "sky"


def test_vuelos_distintos_no_se_colapsan():
    result = dedupe_offers(
        [
            offer(flight_number="LA2034", hour=8),
            offer(flight_number="LA2036", hour=14),
            offer(airline="Sky Airline", flight_number="H25001", hour=8),
        ]
    )

    assert len(result) == 3


def test_mismo_numero_a_distinta_hora_son_vuelos_distintos():
    """Un mismo número de vuelo opera varias veces al día: no es el mismo vuelo."""
    result = dedupe_offers([offer(hour=6), offer(hour=18)])

    assert len(result) == 2


def test_la_aerolinea_se_compara_sin_importar_mayusculas_ni_espacios():
    result = dedupe_offers(
        [
            offer(airline="LATAM", price="450.00"),
            offer(airline="  latam  ", price="420.00"),
        ]
    )

    assert len(result) == 1
    assert result[0].price_pen == Decimal("420.00")


def test_ofertas_sin_identidad_no_se_colapsan():
    """Sin número de vuelo ni horario no hay forma de saber si son el mismo vuelo."""
    anonymous = RawFlightOffer(
        origin="LIM",
        destination="CUZ",
        search_date=SEARCH_DATE,
        price_pen=Decimal("300.00"),
        source="google_flights",
    )
    result = dedupe_offers([anonymous, anonymous])

    assert len(result) == 2


def test_el_resultado_viene_ordenado_por_precio():
    result = dedupe_offers(
        [
            offer(flight_number="LA1", hour=6, price="700.00"),
            offer(flight_number="LA2", hour=9, price="250.00"),
            offer(flight_number="LA3", hour=12, price="480.00"),
        ]
    )

    assert [o.price_pen for o in result] == [
        Decimal("250.00"),
        Decimal("480.00"),
        Decimal("700.00"),
    ]


def test_lista_vacia():
    assert dedupe_offers([]) == []
