"""Parseo del payload de Google Flights, sin red.

El payload real es un array anidado sin nombres de campo (ver docstring de
`providers/google_flights.py`). Estos tests fijan los índices observados en
producción para que un cambio de Google se detecte aquí y no en el bot.
"""

from datetime import date
from decimal import Decimal

from apps.scraping.providers.google_flights import PERU_TZ, GoogleFlightsProvider

SEARCH_DATE = date(2026, 8, 26)


def build_leg(
    *,
    from_code,
    to_code,
    departure_time,
    arrival_time,
    carrier="JA",
    number="7019",
    airline="JetSMART",
    departure_date=(2026, 8, 26),
    arrival_date=(2026, 8, 26),
):
    """Arma un tramo con la forma exacta que devuelve Google."""
    leg = [None] * 23
    leg[2] = "Jetsmart Airlines Peru S.a.c."
    leg[3] = from_code
    leg[4] = f"Aeropuerto de {from_code}"
    leg[5] = f"Aeropuerto de {to_code}"
    leg[6] = to_code
    leg[8] = departure_time
    leg[10] = arrival_time
    leg[11] = 80
    leg[17] = "Airbus A320"
    leg[20] = list(departure_date)
    leg[21] = list(arrival_date)
    leg[22] = [carrier, number, None, airline]
    return leg


def build_itinerary(*, legs, price, carrier="JA", airlines=("JetSMART",)):
    """`item[0]` = itinerario, `item[1]` = bloque de precio."""
    price_block = [[None, price] if price is not None else [], "token-opaco"]
    return [[carrier, list(airlines), legs], price_block]


def build_payload(items):
    payload = [None] * 31
    payload[3] = [items, 0, 0, 0, [1]]
    return payload


def parse(payload):
    provider = GoogleFlightsProvider()
    return provider._build_offers(payload, "LIM", "CUZ", SEARCH_DATE, "https://example.test/q")


def test_lee_vuelo_directo_completo():
    payload = build_payload(
        [
            build_itinerary(
                legs=[
                    build_leg(
                        from_code="LIM",
                        to_code="CUZ",
                        departure_time=[5, 30],
                        arrival_time=[6, 50],
                    )
                ],
                price=202,
            )
        ]
    )

    (offer,) = parse(payload)

    assert offer.origin == "LIM"
    assert offer.destination == "CUZ"
    assert offer.price_pen == Decimal("202.00")
    assert offer.original_price is None
    assert offer.original_currency == ""
    assert offer.airline == "JetSMART"
    assert offer.flight_number == "JA7019"
    assert offer.stops == 0
    assert offer.source == "google_flights"
    assert offer.search_date == SEARCH_DATE
    assert offer.departure_dt.hour == 5 and offer.departure_dt.minute == 30
    assert offer.departure_dt.tzinfo is PERU_TZ
    assert offer.arrival_dt.hour == 6 and offer.arrival_dt.minute == 50
    assert offer.deep_link == "https://example.test/q"


def test_omite_tarifas_sin_precio():
    """Sky Airline aparece a veces como 'Ver precio': sin importe, no hay oferta."""
    payload = build_payload(
        [
            build_itinerary(
                legs=[
                    build_leg(
                        from_code="LIM",
                        to_code="CUZ",
                        departure_time=[5, 30],
                        arrival_time=[6, 50],
                    )
                ],
                price=None,
            ),
            build_itinerary(
                legs=[
                    build_leg(
                        from_code="LIM",
                        to_code="CUZ",
                        departure_time=[8],
                        arrival_time=[9, 20],
                        number="7021",
                    )
                ],
                price=214,
            ),
        ]
    )

    offers = parse(payload)

    assert len(offers) == 1
    assert offers[0].flight_number == "JA7021"


def test_hora_con_componentes_omitidos():
    """Google omite los ceros: [8] es 08:00 y [None, 31] es 00:31."""
    payload = build_payload(
        [
            build_itinerary(
                legs=[
                    build_leg(
                        from_code="LIM",
                        to_code="IQT",
                        departure_time=[8],
                        arrival_time=[None, 31],
                        arrival_date=(2026, 8, 27),
                    )
                ],
                price=310,
            )
        ]
    )

    (offer,) = parse(payload)

    assert (offer.departure_dt.hour, offer.departure_dt.minute) == (8, 0)
    assert (offer.arrival_dt.hour, offer.arrival_dt.minute) == (0, 31)
    assert offer.arrival_dt.date() == date(2026, 8, 27)


def test_itinerario_con_escala_cuenta_tramos_y_encadena_numeros():
    payload = build_payload(
        [
            build_itinerary(
                legs=[
                    build_leg(
                        from_code="AQP",
                        to_code="LIM",
                        departure_time=[6],
                        arrival_time=[7, 40],
                        carrier="LA",
                        number="2011",
                    ),
                    build_leg(
                        from_code="LIM",
                        to_code="PEM",
                        departure_time=[11],
                        arrival_time=[13, 5],
                        carrier="LA",
                        number="2233",
                    ),
                ],
                price=612,
                carrier="LA",
                airlines=("LATAM",),
            )
        ]
    )

    (offer,) = parse(payload)

    assert offer.origin == "AQP"
    assert offer.destination == "PEM"
    assert offer.stops == 1
    assert offer.flight_number == "LA2011/LA2233"
    assert offer.departure_dt.hour == 6
    assert offer.arrival_dt.hour == 13


def test_varias_aerolineas_se_concatenan():
    payload = build_payload(
        [
            build_itinerary(
                legs=[
                    build_leg(
                        from_code="LIM",
                        to_code="CUZ",
                        departure_time=[5, 30],
                        arrival_time=[6, 50],
                    )
                ],
                price=202,
                airlines=("LATAM", "Sky Airline"),
            )
        ]
    )

    (offer,) = parse(payload)

    assert offer.airline == "LATAM / Sky Airline"


def test_itinerario_corrupto_no_tumba_la_busqueda():
    payload = build_payload(
        [
            [["JA", ["JetSMART"], None], [[None, 202], "token"]],  # sin tramos
            ["basura"],  # forma inesperada
            build_itinerary(
                legs=[
                    build_leg(
                        from_code="LIM",
                        to_code="CUZ",
                        departure_time=[5, 30],
                        arrival_time=[6, 50],
                    )
                ],
                price=202,
            ),
        ]
    )

    offers = parse(payload)

    assert len(offers) == 1
    assert offers[0].price_pen == Decimal("202.00")


def test_payload_sin_bloque_de_itinerarios_devuelve_vacio():
    assert parse([None] * 31) == []
    assert parse(build_payload([])) == []
