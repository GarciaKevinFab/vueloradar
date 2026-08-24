"""Barrido: snapshots, tolerancia a fallos y pausa automática de la fuente."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.scraping import ratelimit

LIMA = ZoneInfo("America/Lima")
SEARCH_DATE = date(2026, 9, 15)
SOURCE = "google_flights"


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def route(peru_airports):
    from apps.flights.models import Route

    return Route.objects.create(
        origin_id="LIM", destination_id="CUZ", is_monitored=True, priority=1
    )


def fake_offer(price, airline="LATAM", hour=8):
    from apps.flights.models import FlightOffer

    return FlightOffer(
        airline=airline, flight_number="LA2034",
        departure_dt=datetime(2026, 9, 15, hour, 0, tzinfo=LIMA),
        arrival_dt=datetime(2026, 9, 15, hour + 1, 20, tzinfo=LIMA),
        stops=0, price_pen=Decimal(price), source="google_flights",
        search_date=SEARCH_DATE,
    )


# ------------------------------------------------------------ camino feliz
@pytest.mark.django_db
def test_scan_route_date_crea_el_snapshot(route):
    from apps.flights.models import PriceSnapshot
    from apps.scraping.tasks import scan_route_date

    offers = [fake_offer("450", "LATAM", 8), fake_offer("202", "JetSMART", 6), fake_offer("300")]
    with patch("apps.scraping.tasks.search_and_store", return_value=offers):
        result = scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()]).get()

    assert result["status"] == "ok"
    assert result["offers"] == 3

    snapshot = PriceSnapshot.objects.get(route=route)
    assert snapshot.min_price_pen == Decimal("202.00")
    assert snapshot.avg_price_pen == Decimal("317.33")
    assert snapshot.offers_count == 3
    assert snapshot.cheapest_airline == "JetSMART"
    assert snapshot.flight_date == SEARCH_DATE


def test_el_provider_limpia_los_fallos_al_responder():
    """El conteo vive en el provider: es el único que distingue error de vacío."""
    from apps.scraping.providers.google_flights import GoogleFlightsProvider

    ratelimit.record_failure(SOURCE)
    ratelimit.record_failure(SOURCE)

    provider = GoogleFlightsProvider()
    with patch("apps.scraping.providers.google_flights.fetch_flights_html", return_value="<html/>"),          patch("apps.scraping.providers.google_flights._extract_payload", return_value=[None] * 31),          patch.object(provider._throttle, "wait"):
        offers = provider.search("LIM", "ANS", SEARCH_DATE)

    assert offers == []
    assert ratelimit.failure_count(SOURCE) == 0, "vacío no es fallo: la consulta funcionó"


def test_el_provider_cuenta_el_fallo_cuando_google_explota():
    from apps.scraping.providers.google_flights import GoogleFlightsProvider

    provider = GoogleFlightsProvider()
    with patch("apps.scraping.providers.google_flights.fetch_flights_html",
               side_effect=RuntimeError("502 de Google")),          patch.object(provider._throttle, "wait"):
        offers = provider.search("LIM", "CUZ", SEARCH_DATE)

    assert offers == []
    assert ratelimit.failure_count(SOURCE) == 1


# --------------------------------------------------------------- tolerancia
@pytest.mark.django_db
def test_un_provider_que_explota_no_tumba_la_task(route):
    from apps.flights.models import PriceSnapshot
    from apps.scraping.tasks import scan_route_date

    with patch("apps.scraping.tasks.search_and_store", side_effect=RuntimeError("Google cayo")):
        result = scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()]).get()

    assert result["status"] == "error"
    assert PriceSnapshot.objects.count() == 0
    assert ratelimit.failure_count(SOURCE) == 1


@pytest.mark.django_db
def test_sin_ofertas_no_crea_snapshot_ni_cuenta_como_fallo_de_fuente(route):
    """Hay rutas peruanas sin vuelo en muchas fechas: vacío es un dato, no una falla.

    Contarlo como fallo pausaba la fuente en medio del barrido y mataba el resto.
    """
    from apps.flights.models import PriceSnapshot
    from apps.scraping.tasks import scan_route_date

    with patch("apps.scraping.tasks.search_and_store", return_value=[]):
        result = scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()]).get()

    assert result["status"] == "empty"
    assert PriceSnapshot.objects.count() == 0
    assert ratelimit.failure_count(SOURCE) == 0
    assert ratelimit.is_paused(SOURCE) is False


@pytest.mark.django_db
def test_muchos_vacios_seguidos_no_pausan_la_fuente(route):
    """Regresión: las rutas chicas se barren juntas y encadenan vacíos legítimos."""
    from apps.scraping.tasks import scan_route_date

    with patch("apps.scraping.tasks.search_and_store", return_value=[]):
        for _ in range(10):
            scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()]).get()

    assert ratelimit.is_paused(SOURCE) is False


@pytest.mark.django_db
def test_ruta_inexistente_se_saltea():
    from apps.scraping.tasks import scan_route_date

    result = scan_route_date.apply(args=[999999, SEARCH_DATE.isoformat()]).get()
    assert result == {"status": "skipped", "reason": "route_missing", "route_id": 999999}


@pytest.mark.django_db
def test_aeropuerto_desconocido_se_saltea_sin_contar_fallo_de_fuente(route):
    from apps.scraping.services import UnknownAirportError
    from apps.scraping.tasks import scan_route_date

    with patch("apps.scraping.tasks.search_and_store", side_effect=UnknownAirportError("XXX")):
        result = scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()]).get()

    assert result["reason"] == "unknown_airport"
    assert ratelimit.failure_count(SOURCE) == 0


# ------------------------------------------------------------ pausa de fuente
@pytest.mark.django_db
def test_tres_fallos_seguidos_pausan_la_fuente(route, settings):
    from apps.scraping.tasks import scan_route_date

    settings.SOURCE_MAX_CONSECUTIVE_FAILURES = 3

    with patch("apps.scraping.tasks.search_and_store", side_effect=RuntimeError("Google cayo")):
        for intento in range(1, 4):
            scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()]).get()
            if intento < 3:
                assert not ratelimit.is_paused(SOURCE), f"pausada demasiado pronto ({intento})"

    assert ratelimit.is_paused(SOURCE) is True


@pytest.mark.django_db
def test_la_pausa_avisa_al_admin(settings):
    from apps.scraping.tasks import pause_source

    settings.SOURCE_PAUSE_SECONDS = 1800
    with patch("apps.scraping.tasks.send_admin_alert", return_value=True) as alert:
        result = pause_source.apply(args=[SOURCE]).get()

    assert result["paused_seconds"] == 1800
    assert result["admin_notified"] is True
    assert SOURCE in alert.call_args[0][0]


@pytest.mark.django_db
def test_con_la_fuente_pausada_la_task_se_reencola_en_vez_de_descartarse(route):
    """Saltear vaciaba la cola en segundos y se perdía el resto del barrido."""
    from apps.scraping.tasks import scan_route_date

    ratelimit.pause(SOURCE, 1800)
    with patch("apps.scraping.tasks.search_and_store") as buscar,          patch.object(scan_route_date, "retry", side_effect=Exception("reencolada")) as reintento:
        with pytest.raises(Exception, match="reencolada"):
            scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()], throw=True).get()

    buscar.assert_not_called()
    assert reintento.call_args.kwargs["countdown"] > 1800


@pytest.mark.django_db
def test_pausa_que_sobrevive_a_los_reintentos_degrada_sin_reventar(route):
    """Si la fuente sigue rota tras agotar reintentos, se abandona en silencio."""
    from apps.scraping.tasks import scan_route_date

    ratelimit.pause(SOURCE, 1800)
    with patch("apps.scraping.tasks.search_and_store") as buscar:
        result = scan_route_date.apply(args=[route.pk, SEARCH_DATE.isoformat()]).get()

    assert result["status"] == "skipped"
    assert result["reason"] == "source_paused"
    assert result["route"] == str(route)
    buscar.assert_not_called()


# ---------------------------------------------------------- barrido completo
@pytest.mark.django_db
def test_scan_all_monitored_encola_rutas_por_fechas(peru_airports):
    from apps.flights.models import Route
    from apps.scraping.tasks import scan_all_monitored

    Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True, priority=1)
    Route.objects.create(origin_id="LIM", destination_id="AQP", is_monitored=True, priority=2)
    Route.objects.create(origin_id="LIM", destination_id="PEM", is_monitored=False)

    with patch("apps.scraping.tasks.scan_route_date.apply_async") as encolar, \
         patch("apps.scraping.tasks.compute_route_stats.apply_async") as stats:
        result = scan_all_monitored.apply().get()

    assert result["routes"] == 2, "la ruta sin monitorear no entra"
    assert result["dates"] == 30
    assert result["tasks"] == 60
    assert encolar.call_count == 60
    assert encolar.call_args_list[0].kwargs["queue"] == "scraping"
    stats.assert_called_once()


@pytest.mark.django_db
def test_scan_all_monitored_filtra_por_prioridad(peru_airports):
    from apps.flights.models import Route
    from apps.scraping.tasks import scan_all_monitored

    Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True, priority=1)
    Route.objects.create(origin_id="LIM", destination_id="AQP", is_monitored=True, priority=2)

    with patch("apps.scraping.tasks.scan_route_date.apply_async"), \
         patch("apps.scraping.tasks.compute_route_stats.apply_async"):
        result = scan_all_monitored.apply(args=[1]).get()

    assert result["routes"] == 1


@pytest.mark.django_db
def test_sin_rutas_monitoreadas_no_encola_nada(peru_airports):
    from apps.scraping.tasks import scan_all_monitored

    with patch("apps.scraping.tasks.scan_route_date.apply_async") as encolar:
        result = scan_all_monitored.apply().get()

    assert result == {"routes": 0, "dates": 0, "tasks": 0}
    encolar.assert_not_called()


# ------------------------------------------------------------------- purga
@pytest.mark.django_db
def test_purge_borra_ofertas_viejas_y_conserva_snapshots(route):
    from apps.flights.models import FlightOffer, PriceSnapshot
    from apps.scraping.tasks import purge_old_offers

    vieja = FlightOffer.objects.create(
        route=route, price_pen=Decimal("300"), source="google_flights", search_date=SEARCH_DATE
    )
    FlightOffer.objects.create(
        route=route, price_pen=Decimal("200"), source="google_flights", search_date=SEARCH_DATE
    )
    FlightOffer.objects.filter(pk=vieja.pk).update(scraped_at=timezone.now() - timedelta(days=120))

    snapshot = PriceSnapshot.objects.create(
        route=route, flight_date=SEARCH_DATE,
        min_price_pen=Decimal("200"), avg_price_pen=Decimal("250"), offers_count=2,
    )
    PriceSnapshot.objects.filter(pk=snapshot.pk).update(
        snapshot_at=timezone.now() - timedelta(days=400)
    )

    result = purge_old_offers.apply().get()

    assert result["deleted"] == 1
    assert FlightOffer.objects.count() == 1
    assert PriceSnapshot.objects.count() == 1, "los snapshots no se purgan nunca"
