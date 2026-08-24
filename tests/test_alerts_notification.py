"""Mensaje de alerta, envío y límites por plan."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.ai_analyst.analyst import Verdict
from apps.alerts.models import Alert, AlertTrigger
from apps.alerts.notifications import build_alert_message
from apps.alerts.services import AlertLimitReached, create_alert, deactivate, get_quota, list_alerts

FECHA_VUELO = date(2026, 10, 14)


@pytest.fixture
def route(peru_airports):
    from apps.flights.models import Route

    return Route.objects.create(origin_id="LIM", destination_id="PEM", is_monitored=True)


@pytest.fixture
def user(db):
    from apps.users.models import TelegramUser

    return TelegramUser.objects.create(telegram_id=500000001, first_name="Kevin")


@pytest.fixture
def trigger(route, user):
    from apps.flights.models import PriceSnapshot, RouteStats

    RouteStats.objects.create(
        route=route, avg_30d=Decimal("221"), min_30d=Decimal("168"),
        p25_30d=Decimal("190"), median_30d=Decimal("215"), samples_count=30,
    )
    snapshot = PriceSnapshot.objects.create(
        route=route, flight_date=FECHA_VUELO, min_price_pen=Decimal("152"),
        avg_price_pen=Decimal("190"), offers_count=6, cheapest_airline="Sky",
    )
    alerta = Alert.objects.create(user=user, route=route, alert_type=Alert.TYPE_DEAL_DETECTED)
    return AlertTrigger.objects.create(alert=alerta, snapshot=snapshot, price_pen=Decimal("152"))


# ------------------------------------------------------------------- mensaje
@pytest.mark.django_db
def test_mensaje_con_veredicto(trigger):
    veredicto = Verdict(
        action="comprar", confidence=85,
        reason="Mínimo histórico de 30d era S/ 168, este precio lo rompe.",
        provider="anthropic",
    )
    with patch("apps.ai_analyst.analyst.get_verdict", return_value=veredicto):
        texto = build_alert_message(trigger)

    assert "Oferta detectada" in texto
    assert "LIM → PEM" in texto
    assert "S/ 152" in texto
    assert "Sky" in texto
    assert "14 oct" in texto
    assert "31% bajo" in texto
    assert "S/ 221" in texto
    assert "Veredicto: COMPRA" in texto
    assert "S/ 168" in texto


@pytest.mark.django_db
def test_sin_ia_la_alerta_se_manda_igual(trigger):
    """Criterio de aceptación: todo funciona sin veredicto y sin excepciones."""
    with patch("apps.ai_analyst.analyst.get_verdict", return_value=None):
        texto = build_alert_message(trigger)

    assert "Oferta detectada" in texto
    assert "S/ 152" in texto
    assert "31% bajo" in texto
    assert "Veredicto" not in texto


@pytest.mark.django_db
def test_si_el_analista_explota_la_alerta_se_manda_igual(trigger):
    with patch("apps.ai_analyst.analyst.get_verdict", side_effect=RuntimeError("boom")):
        texto = build_alert_message(trigger)

    assert "S/ 152" in texto
    assert "Veredicto" not in texto


@pytest.mark.django_db
def test_price_below_usa_otro_encabezado(trigger):
    trigger.alert.alert_type = Alert.TYPE_PRICE_BELOW
    trigger.alert.target_price_pen = Decimal("180")
    trigger.alert.save()

    with patch("apps.ai_analyst.analyst.get_verdict", return_value=None):
        texto = build_alert_message(trigger)

    assert "Bajó de tu precio objetivo" in texto


# -------------------------------------------------------------------- envío
@pytest.mark.django_db
def test_la_task_marca_el_trigger_como_enviado(trigger):
    from apps.alerts.tasks import send_alert_notification

    with patch("apps.ai_analyst.analyst.get_verdict", return_value=None), \
         patch("apps.alerts.tasks._send_telegram", return_value=True) as envio:
        resultado = send_alert_notification.apply(args=[trigger.pk]).get()

    assert resultado["status"] == "sent"
    envio.assert_called_once()
    assert envio.call_args.args[0] == 500000001
    trigger.refresh_from_db()
    assert trigger.message_sent is True


@pytest.mark.django_db
def test_si_telegram_falla_no_se_marca_enviado(trigger):
    from apps.alerts.tasks import send_alert_notification

    with patch("apps.ai_analyst.analyst.get_verdict", return_value=None), \
         patch("apps.alerts.tasks._send_telegram", return_value=False):
        resultado = send_alert_notification.apply(args=[trigger.pk]).get()

    assert resultado["status"] == "failed"
    trigger.refresh_from_db()
    assert trigger.message_sent is False


@pytest.mark.django_db
def test_no_reenvia_un_trigger_ya_enviado(trigger):
    from apps.alerts.tasks import send_alert_notification

    AlertTrigger.objects.filter(pk=trigger.pk).update(message_sent=True)

    with patch("apps.alerts.tasks._send_telegram") as envio:
        resultado = send_alert_notification.apply(args=[trigger.pk]).get()

    assert resultado["status"] == "already_sent"
    envio.assert_not_called()


# ------------------------------------------------------------------ límites
@pytest.mark.django_db
def test_free_topea_en_dos_alertas(route, user, peru_airports, settings):
    from apps.flights.models import Route

    settings.FREE_MAX_ALERTS = 2
    otra = Route.objects.create(origin_id="LIM", destination_id="CUZ")
    tercera = Route.objects.create(origin_id="LIM", destination_id="AQP")

    create_alert(user, route)
    create_alert(user, otra)

    with pytest.raises(AlertLimitReached) as exc:
        create_alert(user, tercera)

    assert exc.value.limit == 2
    assert get_quota(user).remaining == 0


@pytest.mark.django_db
def test_premium_llega_a_veinte(route, user, settings):
    from apps.users.models import TelegramUser

    settings.PREMIUM_MAX_ALERTS = 20
    user.plan = TelegramUser.PLAN_PREMIUM
    user.save()

    assert get_quota(user).limit == 20


@pytest.mark.django_db
def test_repetir_el_comando_no_duplica_ni_gasta_cupo(route, user, settings):
    settings.FREE_MAX_ALERTS = 2

    _a, creada = create_alert(user, route, target_price=Decimal("180"))
    assert creada is True

    _b, creada = create_alert(user, route, target_price=Decimal("180"))
    assert creada is False
    assert Alert.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_repetir_con_otro_precio_actualiza_el_objetivo(route, user):
    create_alert(user, route, target_price=Decimal("180"))
    alerta, creada = create_alert(user, route, target_price=Decimal("150"))

    assert creada is False
    assert alerta.target_price_pen == Decimal("150")


@pytest.mark.django_db
def test_desactivar_libera_cupo(route, user, settings):
    settings.FREE_MAX_ALERTS = 2
    alerta, _ = create_alert(user, route)

    assert get_quota(user).remaining == 1
    assert deactivate(user, alerta.pk) is not None
    assert get_quota(user).remaining == 2
    assert list_alerts(user) == []


@pytest.mark.django_db
def test_no_se_puede_desactivar_la_alerta_de_otro(route, user, db):
    from apps.users.models import TelegramUser

    otro = TelegramUser.objects.create(telegram_id=999, first_name="Ajeno")
    alerta, _ = create_alert(user, route)

    assert deactivate(otro, alerta.pk) is None
    alerta.refresh_from_db()
    assert alerta.is_active is True
