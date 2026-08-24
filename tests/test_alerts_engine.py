"""Motor de alertas: reglas de disparo y anti-spam."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.alerts.engine import evaluate_snapshot, is_deal
from apps.alerts.models import Alert, AlertTrigger

FECHA_VUELO = date(2026, 10, 14)


@pytest.fixture
def route(peru_airports):
    from apps.flights.models import Route

    return Route.objects.create(origin_id="LIM", destination_id="PEM", is_monitored=True)


@pytest.fixture
def user(db):
    from apps.users.models import TelegramUser

    return TelegramUser.objects.create(telegram_id=500000001, first_name="Kevin")


def make_stats(route, *, p25="200", avg="240", samples=30):
    from apps.flights.models import RouteStats

    return RouteStats.objects.create(
        route=route, avg_30d=Decimal(avg), min_30d=Decimal("168"),
        p25_30d=Decimal(p25), median_30d=Decimal("230"), samples_count=samples,
    )


def make_snapshot(route, price, *, flight_date=FECHA_VUELO):
    from apps.flights.models import PriceSnapshot

    return PriceSnapshot.objects.create(
        route=route, flight_date=flight_date,
        min_price_pen=Decimal(price), avg_price_pen=Decimal(price) + 40,
        offers_count=5, cheapest_airline="Sky",
    )


@pytest.fixture(autouse=True)
def no_enviar():
    """Los tests no mandan mensajes de Telegram."""
    with patch("apps.alerts.tasks.send_alert_notification.apply_async") as envio:
        yield envio


# ------------------------------------------------------------ regla is_deal
@pytest.mark.django_db
def test_deal_dispara_bajo_el_90_por_ciento_del_p25(route):
    stats = make_stats(route, p25="200", samples=30)

    assert is_deal(Decimal("180"), stats) is True, "p25*0.90 = 180, el borde entra"
    assert is_deal(Decimal("179"), stats) is True
    assert is_deal(Decimal("181"), stats) is False


@pytest.mark.django_db
def test_deal_no_dispara_con_poco_historico(route, settings):
    settings.DEAL_MIN_SAMPLES = 20
    stats = make_stats(route, p25="200", samples=19)

    assert is_deal(Decimal("100"), stats) is False, "con 19 muestras el p25 no significa nada"

    stats.samples_count = 20
    assert is_deal(Decimal("100"), stats) is True


@pytest.mark.django_db
def test_deal_sin_stats_no_dispara(route):
    assert is_deal(Decimal("50"), None) is False


# --------------------------------------------------------- evaluate_snapshot
@pytest.mark.django_db
def test_price_below_dispara_al_cruzar_el_techo(route, user, no_enviar):
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"),
    )
    snapshot = make_snapshot(route, "152")

    resultado = evaluate_snapshot(snapshot)

    assert len(resultado.triggered) == 1
    assert AlertTrigger.objects.count() == 1
    assert AlertTrigger.objects.first().price_pen == Decimal("152.00")


@pytest.mark.django_db
def test_price_below_no_dispara_por_encima_del_techo(route, user):
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"),
    )
    resultado = evaluate_snapshot(make_snapshot(route, "210"))

    assert resultado.triggered == []
    assert AlertTrigger.objects.count() == 0


@pytest.mark.django_db
def test_deal_detected_dispara_con_historico_suficiente(route, user):
    make_stats(route, p25="200", samples=30)
    Alert.objects.create(user=user, route=route, alert_type=Alert.TYPE_DEAL_DETECTED)

    resultado = evaluate_snapshot(make_snapshot(route, "152"))

    assert len(resultado.triggered) == 1


@pytest.mark.django_db
def test_alerta_inactiva_no_se_evalua(route, user):
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"), is_active=False,
    )
    assert evaluate_snapshot(make_snapshot(route, "100")).triggered == []


@pytest.mark.django_db
def test_alerta_con_fecha_solo_dispara_en_esa_fecha(route, user):
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"), flight_date=FECHA_VUELO,
    )

    otra = make_snapshot(route, "100", flight_date=date(2026, 11, 1))
    assert evaluate_snapshot(otra).triggered == []

    correcta = make_snapshot(route, "100", flight_date=FECHA_VUELO)
    assert len(evaluate_snapshot(correcta).triggered) == 1


@pytest.mark.django_db
def test_alerta_sin_fecha_dispara_en_cualquiera(route, user):
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"), flight_date=None,
    )
    resultado = evaluate_snapshot(make_snapshot(route, "100", flight_date=date(2026, 12, 25)))

    assert len(resultado.triggered) == 1


@pytest.mark.django_db
def test_sin_alertas_no_hace_nada(route):
    assert evaluate_snapshot(make_snapshot(route, "100")).triggered == []


# ------------------------------------------------------------------ anti-spam
@pytest.mark.django_db
def test_no_reavisa_dentro_de_las_12_horas(route, user, settings):
    settings.ALERT_COOLDOWN_HOURS = 12
    alerta = Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"),
    )

    primero = evaluate_snapshot(make_snapshot(route, "152"))
    assert len(primero.triggered) == 1

    # Un precio aún más barato, pero apenas después: sigue en cooldown.
    segundo = evaluate_snapshot(make_snapshot(route, "120"))
    assert segundo.triggered == []
    assert segundo.skipped_cooldown == 1
    assert AlertTrigger.objects.filter(alert=alerta).count() == 1


@pytest.mark.django_db
def test_pasadas_las_12_horas_vuelve_a_avisar_si_bajo_lo_suficiente(route, user, settings):
    settings.ALERT_COOLDOWN_HOURS = 12
    settings.ALERT_MIN_DROP_PCT = Decimal("5")
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"),
    )

    evaluate_snapshot(make_snapshot(route, "152"))
    _envejecer_triggers(horas=13)

    resultado = evaluate_snapshot(make_snapshot(route, "140"))
    assert len(resultado.triggered) == 1, "-8% respecto de 152, supera el umbral"


@pytest.mark.django_db
def test_no_reavisa_si_el_precio_bajo_menos_del_5_por_ciento(route, user, settings):
    settings.ALERT_COOLDOWN_HOURS = 12
    settings.ALERT_MIN_DROP_PCT = Decimal("5")
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("180"),
    )

    evaluate_snapshot(make_snapshot(route, "152"))
    _envejecer_triggers(horas=13)

    resultado = evaluate_snapshot(make_snapshot(route, "149"))
    assert resultado.triggered == [], "-2% no justifica otro mensaje"
    assert resultado.skipped_small_drop == 1


@pytest.mark.django_db
def test_el_borde_del_5_por_ciento_si_dispara(route, user, settings):
    settings.ALERT_COOLDOWN_HOURS = 12
    settings.ALERT_MIN_DROP_PCT = Decimal("5")
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("300"),
    )

    evaluate_snapshot(make_snapshot(route, "200"))
    _envejecer_triggers(horas=13)

    resultado = evaluate_snapshot(make_snapshot(route, "190"))
    assert len(resultado.triggered) == 1, "exactamente -5% entra"


@pytest.mark.django_db
def test_un_precio_mas_caro_no_reavisa(route, user, settings):
    settings.ALERT_COOLDOWN_HOURS = 12
    Alert.objects.create(
        user=user, route=route, alert_type=Alert.TYPE_PRICE_BELOW,
        target_price_pen=Decimal("300"),
    )

    evaluate_snapshot(make_snapshot(route, "150"))
    _envejecer_triggers(horas=20)

    resultado = evaluate_snapshot(make_snapshot(route, "280"))
    assert resultado.triggered == []


def _envejecer_triggers(*, horas: int) -> None:
    """Mueve todos los triggers al pasado para saltar el cooldown."""
    AlertTrigger.objects.update(triggered_at=timezone.now() - timedelta(hours=horas))
