"""Estadísticas de ruta: percentiles y el task que las persiste."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.flights.stats import compute_stats, percentile

PRICES = [Decimal(p) for p in ("100", "200", "300", "400")]


# ------------------------------------------------------------- lógica pura
def test_percentiles_con_interpolacion_lineal():
    assert percentile(PRICES, 0.25) == Decimal("175.00")
    assert percentile(PRICES, 0.50) == Decimal("250.00")
    assert percentile(PRICES, 0.75) == Decimal("325.00")


def test_percentiles_en_los_extremos():
    assert percentile(PRICES, 0.0) == Decimal("100.00")
    assert percentile(PRICES, 1.0) == Decimal("400.00")


def test_percentil_no_depende_del_orden_de_entrada():
    revueltos = [Decimal("300"), Decimal("100"), Decimal("400"), Decimal("200")]
    assert percentile(revueltos, 0.25) == percentile(PRICES, 0.25)


def test_percentil_con_una_sola_muestra():
    assert percentile([Decimal("250")], 0.25) == Decimal("250.00")


def test_percentil_de_lista_vacia():
    assert percentile([], 0.5) is None


def test_fraccion_fuera_de_rango():
    with pytest.raises(ValueError):
        percentile(PRICES, 1.5)


def test_compute_stats_completo():
    result = compute_stats(PRICES)
    assert result.avg == Decimal("250.00")
    assert result.minimum == Decimal("100.00")
    assert result.p25 == Decimal("175.00")
    assert result.median == Decimal("250.00")
    assert result.samples == 4
    assert result.is_empty is False


def test_compute_stats_sin_datos():
    result = compute_stats([])
    assert result.is_empty is True
    assert (result.avg, result.minimum, result.p25, result.median) == (None, None, None, None)


def test_promedio_redondea_a_centavos():
    assert compute_stats([Decimal("100"), Decimal("101")]).avg == Decimal("100.50")
    assert compute_stats([Decimal("100"), Decimal("101"), Decimal("101")]).avg == Decimal("100.67")


# --------------------------------------------------- integración con el task
@pytest.mark.django_db
def test_compute_route_stats_persiste_la_ventana_de_30_dias(peru_airports):
    from apps.flights.models import PriceSnapshot, Route, RouteStats
    from apps.scraping.tasks import compute_route_stats

    route = Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True)
    for price in ("100", "200", "300", "400"):
        PriceSnapshot.objects.create(
            route=route, flight_date=date(2026, 9, 15),
            min_price_pen=Decimal(price), avg_price_pen=Decimal(price) + 50,
            offers_count=5, cheapest_airline="LATAM",
        )

    result = compute_route_stats.apply(args=[route.pk]).get()

    assert result["updated"] == 1
    stats = RouteStats.objects.get(route=route)
    assert stats.avg_30d == Decimal("250.00")
    assert stats.min_30d == Decimal("100.00")
    assert stats.p25_30d == Decimal("175.00")
    assert stats.median_30d == Decimal("250.00")
    assert stats.samples_count == 4
    assert stats.has_enough_history is False


@pytest.mark.django_db
def test_ignora_snapshots_fuera_de_la_ventana(peru_airports):
    from apps.flights.models import PriceSnapshot, Route, RouteStats
    from apps.scraping.tasks import compute_route_stats

    route = Route.objects.create(origin_id="LIM", destination_id="AQP")
    reciente = PriceSnapshot.objects.create(
        route=route, flight_date=date(2026, 9, 15),
        min_price_pen=Decimal("200"), avg_price_pen=Decimal("250"), offers_count=3,
    )
    viejo = PriceSnapshot.objects.create(
        route=route, flight_date=date(2026, 9, 15),
        min_price_pen=Decimal("999"), avg_price_pen=Decimal("999"), offers_count=3,
    )
    # auto_now_add no acepta valores, así que se corrige por UPDATE directo.
    PriceSnapshot.objects.filter(pk=viejo.pk).update(
        snapshot_at=timezone.now() - timedelta(days=45)
    )

    compute_route_stats.apply(args=[route.pk]).get()

    stats = RouteStats.objects.get(route=route)
    assert stats.samples_count == 1, "el snapshot de hace 45 días no debe contar"
    assert stats.min_30d == reciente.min_price_pen


@pytest.mark.django_db
def test_ruta_sin_snapshots_no_genera_stats(peru_airports):
    from apps.flights.models import Route, RouteStats
    from apps.scraping.tasks import compute_route_stats

    route = Route.objects.create(origin_id="LIM", destination_id="PEM")
    result = compute_route_stats.apply(args=[route.pk]).get()

    assert result == {"updated": 0, "skipped": 1}
    assert not RouteStats.objects.filter(route=route).exists()


@pytest.mark.django_db
def test_recalcular_actualiza_en_vez_de_duplicar(peru_airports):
    from apps.flights.models import PriceSnapshot, Route, RouteStats
    from apps.scraping.tasks import compute_route_stats

    route = Route.objects.create(origin_id="CUZ", destination_id="LIM")
    PriceSnapshot.objects.create(
        route=route, flight_date=date(2026, 9, 15),
        min_price_pen=Decimal("300"), avg_price_pen=Decimal("300"), offers_count=1,
    )
    compute_route_stats.apply(args=[route.pk]).get()

    PriceSnapshot.objects.create(
        route=route, flight_date=date(2026, 9, 16),
        min_price_pen=Decimal("100"), avg_price_pen=Decimal("100"), offers_count=1,
    )
    compute_route_stats.apply(args=[route.pk]).get()

    assert RouteStats.objects.filter(route=route).count() == 1
    stats = RouteStats.objects.get(route=route)
    assert stats.samples_count == 2
    assert stats.min_30d == Decimal("100.00")
