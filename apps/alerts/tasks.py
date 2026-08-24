"""Envío de notificaciones de alerta por Telegram."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from .models import AlertTrigger
from .notifications import build_alert_message

logger = logging.getLogger(__name__)


@shared_task(bind=True, acks_late=True, max_retries=3, retry_backoff=True, retry_jitter=True)
def send_alert_notification(self, trigger_id: int) -> dict:
    """Manda el aviso al usuario. Marca el trigger como enviado si sale bien."""
    try:
        trigger = (
            AlertTrigger.objects.select_related(
                "alert", "alert__user", "alert__route",
                "alert__route__origin", "alert__route__destination", "snapshot",
            ).get(pk=trigger_id)
        )
    except AlertTrigger.DoesNotExist:
        logger.error("alerts: el trigger %s ya no existe", trigger_id)
        return {"status": "missing", "trigger_id": trigger_id}

    if trigger.message_sent:
        return {"status": "already_sent", "trigger_id": trigger_id}

    _verify_price(trigger)

    texto = build_alert_message(trigger)
    enviado = _send_telegram(trigger.alert.user.telegram_id, texto)

    if enviado:
        AlertTrigger.objects.filter(pk=trigger.pk).update(message_sent=True)
        logger.info("alerts: aviso enviado a %s", trigger.alert.user.telegram_id)
        return {"status": "sent", "trigger_id": trigger_id}

    logger.warning("alerts: no se pudo enviar el aviso %s", trigger_id)
    return {"status": "failed", "trigger_id": trigger_id}


def _verify_price(trigger) -> None:
    """Confirma el precio con la aerolínea antes de avisar (solo deal_detected).

    Una alerta de oferta le dice al usuario "esto es barato de verdad", así que
    conviene que el número sea el que va a ver en el checkout. Las de precio
    objetivo no lo necesitan: el usuario ya fijó su umbral.
    """
    if not settings.VERIFY_DEALS_WITH_DIRECT_SCRAPER:
        return
    if trigger.alert.alert_type != trigger.alert.TYPE_DEAL_DETECTED:
        return

    try:
        from apps.scraping.verification import verify_price

        ruta = trigger.alert.route
        resultado = verify_price(
            ruta.origin_id, ruta.destination_id, trigger.snapshot.flight_date, trigger.price_pen
        )
    except Exception:  # noqa: BLE001 - la alerta se manda igual
        logger.exception("alerts: fallo verificando el precio, se usa el de Google")
        return

    if resultado.verified and resultado.price != trigger.price_pen:
        AlertTrigger.objects.filter(pk=trigger.pk).update(price_pen=resultado.price)
        trigger.price_pen = resultado.price
        logger.info(
            "alerts: precio corregido a S/ %s segun %s", resultado.price, resultado.source
        )


def _send_telegram(chat_id: int, text: str) -> bool:
    """POST directo a la API de Telegram: la task es sync, el bot es async."""
    import json
    import urllib.error
    import urllib.request

    token = settings.TELEGRAM_TOKEN
    if not token:
        logger.warning("alerts: sin TELEGRAM_TOKEN, aviso no enviado")
        return False

    payload = json.dumps(
        {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("ok"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.error("alerts: fallo al enviar a Telegram: %s", exc)
        return False


@shared_task(bind=True, acks_late=True, max_retries=2, retry_backoff=True)
def send_weekly_digest(self) -> dict:
    """Resumen semanal para quienes tienen alertas activas.

    Corre los domingos. Solo se manda a quien tenga alertas: es un recordatorio
    de que el radar sigue mirando, no un boletín para todos.
    """
    from .digest import build_digest, users_with_alerts

    enviados = omitidos = 0
    for user in users_with_alerts():
        texto = build_digest(user)
        if not texto:
            omitidos += 1
            continue
        if _send_telegram(user.telegram_id, texto):
            enviados += 1
        else:
            omitidos += 1

    logger.info("alerts: resumen semanal enviado a %d usuarios (%d omitidos)", enviados, omitidos)
    return {"sent": enviados, "skipped": omitidos}
