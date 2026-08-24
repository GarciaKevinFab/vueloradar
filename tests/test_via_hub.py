"""Combinación de tramos vía LIM para rutas sin vuelo directo.

Todo mockeado: ni red ni proveedores reales.
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from apps.scraping.providers.base import RawFlightOffer
from apps.scraping.services import combine_via_hub, search_and_store

LIMA = ZoneInfo("America/Lima")
SEARCH_DATE = date(2026, 9, 15)


def leg(origin, destination, dep_hour, arr_hour, price, *, flight_number="XX100", airline="LATAM"):
    return RawFlightOffer(
        origin=origin,
        destination=destination,
        search_date=SEARCH_DATE,
        price_pen=Decimal(price),
        source="google_flights",
        airline=airline,
        flight_number=flight_number,
        departure_dt=datetime(2026, 9, 15, dep_hour, 0, tzinfo=LIMA),
        arrival_dt=datetime(2026, 9, 15, arr_hour, 0, tzinfo=LIMA),
        stops=0,
        deep_link="https://example.test/tramo1",
    )


# ----------------------------------------------------------- combinación pura
def test_suma_precios_y_marca_una_escala():
    combos = combine_via_hub(
        [leg("AQP", "LIM", 6, 8, "180.00", flight_number="LA2011")],
        [leg("LIM", "PEM", 12, 14, "320.00", flight_number="LA2233")],
    )

    assert len(combos) == 1
    combo = combos[0]
    assert combo.origin == "AQP"
    assert combo.destination == "PEM"
    assert combo.price_pen == Decimal("500.00")
    assert combo.stops == 1
    assert combo.departure_dt.hour == 6
    assert combo.arrival_dt.hour == 14
    assert combo.flight_number == "LA2011/LA2233"
    assert [l.origin for l in combo.legs] == ["AQP", "LIM"]


def test_descarta_conexiones_menores_al_minimo():
    """Aterriza 08:00 y el siguiente sale 09:00: una hora no alcanza."""
    combos = combine_via_hub(
        [leg("AQP", "LIM", 6, 8, "180.00")],
        [leg("LIM", "PEM", 9, 11, "320.00")],
        min_connection_minutes=120,
    )

    assert combos == []


def test_acepta_la_conexion_justa_en_el_minimo():
    combos = combine_via_hub(
        [leg("AQP", "LIM", 6, 8, "180.00")],
        [leg("LIM", "PEM", 10, 12, "320.00")],
        min_connection_minutes=120,
    )

    assert len(combos) == 1


def test_descarta_el_segundo_tramo_que_sale_antes_de_aterrizar():
    combos = combine_via_hub(
        [leg("AQP", "LIM", 14, 16, "180.00")],
        [leg("LIM", "PEM", 9, 11, "320.00")],
    )

    assert combos == []


def test_combina_todos_los_pares_validos_ordenados_por_precio():
    first = [
        leg("AQP", "LIM", 6, 8, "180.00", flight_number="LA1"),
        leg("AQP", "LIM", 7, 9, "150.00", flight_number="LA2"),
    ]
    second = [
        leg("LIM", "PEM", 12, 14, "320.00", flight_number="LA3"),
        leg("LIM", "PEM", 18, 20, "280.00", flight_number="LA4"),
    ]

    combos = combine_via_hub(first, second)

    assert len(combos) == 4
    assert [c.price_pen for c in combos] == [
        Decimal("430.00"),
        Decimal("460.00"),
        Decimal("470.00"),
        Decimal("500.00"),
    ]


def test_ignora_tramos_sin_horario():
    sin_llegada = leg("AQP", "LIM", 6, 8, "180.00")
    sin_llegada.arrival_dt = None

    assert combine_via_hub([sin_llegada], [leg("LIM", "PEM", 12, 14, "320.00")]) == []


def test_respeta_el_limite_de_itinerarios():
    first = [leg("AQP", "LIM", 6, 8, "100.00", flight_number=f"LA{i}") for i in range(5)]
    second = [leg("LIM", "PEM", 12, 14, "200.00", flight_number=f"LA{i}") for i in range(5)]

    assert len(combine_via_hub(first, second, limit=3)) == 3


# ------------------------------------------------- integración con el servicio
@pytest.mark.django_db
def test_ruta_sin_directo_devuelve_itinerarios_via_lim_sin_persistirlos(peru_airports):
    """AQP→PEM no tiene directo: el servicio debe armar la conexión vía LIM."""
    from apps.flights.models import FlightOffer

    responses = {
        ("AQP", "PEM"): [],
        ("AQP", "LIM"): [leg("AQP", "LIM", 6, 8, "180.00", flight_number="LA2011")],
        ("LIM", "PEM"): [leg("LIM", "PEM", 12, 14, "320.00", flight_number="LA2233")],
    }

    class FakeProvider:
        source_name = "google_flights"

        def search(self, origin, dest, date):
            return list(responses[(origin, dest)])

    with patch("apps.scraping.services.get_providers_for_route", return_value=[FakeProvider()]):
        offers = search_and_store("AQP", "PEM", SEARCH_DATE)

    assert len(offers) == 1
    combo = offers[0]
    assert combo.pk is None, "los itinerarios sintéticos no se guardan"
    assert combo.price_pen == Decimal("500.00")
    assert combo.stops == 1

    # Los tramos reales sí quedan en la base, cada uno bajo su propia ruta.
    stored = FlightOffer.objects.all()
    assert stored.count() == 2
    assert {(o.route.origin_id, o.route.destination_id) for o in stored} == {
        ("AQP", "LIM"),
        ("LIM", "PEM"),
    }


@pytest.mark.django_db
def test_ruta_con_directo_persiste_y_no_busca_conexiones(peru_airports):
    from apps.flights.models import FlightOffer

    direct = leg("LIM", "CUZ", 5, 6, "202.00", flight_number="JA7019", airline="JetSMART")

    class FakeProvider:
        source_name = "google_flights"

        def __init__(self):
            self.calls = []

        def search(self, origin, dest, date):
            self.calls.append((origin, dest))
            return [direct]

    provider = FakeProvider()
    with patch("apps.scraping.services.get_providers_for_route", return_value=[provider]):
        offers = search_and_store("LIM", "CUZ", SEARCH_DATE)

    assert provider.calls == [("LIM", "CUZ")]
    assert len(offers) == 1
    assert offers[0].pk is not None
    assert FlightOffer.objects.count() == 1


@pytest.mark.django_db
def test_aeropuerto_desconocido_falla_con_mensaje_util(peru_airports):
    from apps.scraping.services import UnknownAirportError

    with pytest.raises(UnknownAirportError, match="XXX"):
        search_and_store("XXX", "CUZ", SEARCH_DATE)


@pytest.mark.django_db
def test_ruta_nueva_se_crea_sin_monitorear(peru_airports):
    from apps.flights.models import Route

    class EmptyProvider:
        source_name = "google_flights"

        def search(self, origin, dest, date):
            return []

    with patch("apps.scraping.services.get_providers_for_route", return_value=[EmptyProvider()]):
        search_and_store("CUZ", "AQP", SEARCH_DATE)

    route = Route.objects.get(origin_id="CUZ", destination_id="AQP")
    assert route.is_monitored is False
