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


# ------------------------------------------------------------ link de compra
def test_la_busqueda_simple_trae_link_de_compra():
    t = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=date(2026, 10, 15),
        offers=[FakeOffer("201")],
    )

    assert "google.com/travel/flights" in t
    assert "comprar" in t.lower()


def test_el_link_de_ida_y_vuelta_es_uno_solo_y_de_paquete():
    """No dos links de solo ida: el paquete es donde está el mejor precio."""
    from apps.scraping.providers.google_flights import build_search_url

    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA,
    )

    assert t.count("google.com/travel/flights") == 1
    assert "Comprar el ida y vuelta" in t

    ida_sola = build_search_url([("PEM", "LIM", date(2026, 10, 16))])
    assert ida_sola not in t, "no debe ser el link de un solo tramo"


def test_el_url_de_ida_y_vuelta_difiere_del_de_solo_ida():
    from apps.scraping.providers.google_flights import build_search_url

    ida = build_search_url([("PEM", "LIM", date(2026, 10, 16))])
    vuelta_incluida = build_search_url(
        [("PEM", "LIM", date(2026, 10, 16)), ("LIM", "PEM", date(2026, 10, 18))]
    )

    assert ida and vuelta_incluida
    assert ida != vuelta_incluida


def test_dos_tramos_que_no_son_ida_y_vuelta():
    """Cusco a Lima y después Lima a Arequipa no es un round trip."""
    from apps.scraping.providers.google_flights import _is_round_trip

    assert _is_round_trip([
        ("CUZ", "LIM", date(2026, 10, 16)), ("LIM", "AQP", date(2026, 10, 18))
    ]) is False
    assert _is_round_trip([
        ("PEM", "LIM", date(2026, 10, 16)), ("LIM", "PEM", date(2026, 10, 18))
    ]) is True


def test_sin_tramos_no_hay_link():
    assert formatting.buy_link([]) == ""


def test_un_link_roto_no_tumba_el_mensaje():
    with patch("apps.scraping.providers.google_flights.create_query",
               side_effect=RuntimeError("boom")):
        assert formatting.buy_link([("LIM", "CUZ", date(2026, 10, 15))]) == ""


# ------------------------------------------------------------ el paquete real

def test_el_paquete_manda_sobre_la_suma_cuando_es_mas_barato():
    """Sumar dos pasajes era un techo; el paquete es lo que se paga."""
    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA, paquete=[FakeOffer("850"), FakeOffer("910")],
    )
    assert "Total del viaje: S/ 973" in t, "la suma se sigue mostrando"
    assert "Como paquete de ida y vuelta: S/ 850" in t
    assert "S/ 123 menos que comprar los dos pasajes sueltos" in t
    assert "techo" not in t, "con paquete real ya no hay que hablar de techo"


def test_si_el_paquete_sale_mas_caro_recomienda_los_tramos_sueltos():
    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA, paquete=[FakeOffer("1020")],
    )
    assert "Como paquete de ida y vuelta: S/ 1,020" in t
    assert "comprar los dos tramos por separado" in t
    assert "S/ 47 más" in t


def test_sin_paquete_se_conserva_la_suma_como_techo():
    """Si la cotización del paquete falla, la respuesta no empeora: vuelve a lo de antes."""
    t = formatting.format_round_trip(
        origin="PEM", dest="LIM",
        outbound_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        outbound=IDA, inbound=VUELTA, paquete=[],
    )
    assert "Total del viaje: S/ 973" in t
    assert "Como paquete" not in t and "techo" in t


def test_el_proveedor_pide_el_paquete_como_round_trip_de_dos_tramos():
    """Lo que distingue un paquete de dos búsquedas: un solo query round-trip."""
    from unittest.mock import patch

    from apps.scraping.providers import google_flights as gf

    capturado = {}

    def falso_create_query(**kw):
        capturado.update(kw)

        class Q:
            def url(self):
                return "https://google/flights?paquete"
        return Q()

    with (
        patch.object(gf, "create_query", falso_create_query),
        patch.object(gf, "fetch_flights_html", lambda q: ""),
        patch.object(gf, "_extract_payload", lambda html: []),
        patch.object(gf._SourceThrottle, "wait", lambda self: None),
    ):
        ofertas = gf.GoogleFlightsProvider().search_round_trip(
            "LIM", "CUZ", date(2026, 9, 19), date(2026, 9, 23)
        )

    assert ofertas == []
    assert capturado["trip"] == "round-trip"
    ida, vuelta = capturado["flights"]
    assert (ida.from_airport, ida.to_airport, ida.date) == ("LIM", "CUZ", "2026-09-19")
    assert (vuelta.from_airport, vuelta.to_airport, vuelta.date) == ("CUZ", "LIM", "2026-09-23")


def test_el_paquete_nunca_entra_al_historico(peru_airports):
    """Un precio de paquete en la serie de solo ida contaminaría los veredictos."""
    from decimal import Decimal
    from unittest.mock import patch

    from apps.flights.models import FlightOffer, PriceSnapshot
    from apps.scraping import services
    from apps.scraping.providers.base import RawFlightOffer
    from apps.scraping.providers.google_flights import GoogleFlightsProvider

    paquete = RawFlightOffer(
        origin="LIM", destination="CUZ", search_date=date(2026, 9, 19),
        price_pen=Decimal("375"), source="google_flights", airline="LATAM",
    )
    with patch.object(GoogleFlightsProvider, "search_round_trip", lambda self, *a: [paquete]):
        ofertas = services.search_round_trip("LIM", "CUZ", date(2026, 9, 19), date(2026, 9, 23))

    assert [o.price_pen for o in ofertas] == [Decimal("375")]
    assert PriceSnapshot.objects.count() == 0
    assert FlightOffer.objects.count() == 0
