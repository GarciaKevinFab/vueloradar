"""Motor de alertas: decide qué merece una notificación.

Se llama al final de cada `scan_route_date`, con el snapshot recién creado.
Dos reglas:

- **price_below**: el usuario puso un techo y el precio lo cruzó.
- **deal_detected**: el precio cayó en el 10% más barato observado en 30 días.
  Requiere histórico suficiente — un percentil calculado sobre 5 muestras no
  significa nada y generaría falsos positivos constantes.

Encima de ambas hay un anti-spam de dos capas: ni más de un aviso cada 12h por
alerta, ni re-aviso si el precio no bajó al menos un 5% desde el último.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Alert, AlertTrigger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationResult:
    """Qué pasó al evaluar un snapshot contra las alertas de su ruta."""

    triggered: list
    skipped_cooldown: int = 0
    skipped_small_drop: int = 0
    evaluated: int = 0


def evaluate_snapshot(snapshot) -> EvaluationResult:
    """Evalúa todas las alertas activas de la ruta del snapshot.

    Crea un `AlertTrigger` por cada alerta que corresponda y encola su envío.
    Nunca lanza: un fallo acá no debe tumbar el barrido.
    """
    alertas = list(
        Alert.objects.filter(route_id=snapshot.route_id, is_active=True)
        .select_related("user", "route", "route__origin", "route__destination")
    )
    if not alertas:
        return EvaluationResult(triggered=[])

    stats = _get_stats(snapshot.route_id)
    disparadas = []
    cooldown = pequenas = 0

    for alerta in alertas:
        # Una alerta por correo sin confirmar existe pero no avisa: mandarle
        # correo a alguien que no lo pidio es spam, aunque la intencion sea buena.
        if not alerta.puede_notificar:
            continue
        if not alerta.matches_date(snapshot.flight_date):
            continue
        if not _rule_matches(alerta, snapshot, stats):
            continue

        motivo = _antispam_reason(alerta, snapshot.min_price_pen)
        if motivo == "cooldown":
            cooldown += 1
            continue
        if motivo == "small_drop":
            pequenas += 1
            continue

        trigger = _create_trigger(alerta, snapshot)
        if trigger is not None:
            disparadas.append(trigger)

    if disparadas:
        logger.info(
            "alerts: %d alertas disparadas por %s %s a S/ %s",
            len(disparadas), snapshot.route, snapshot.flight_date, snapshot.min_price_pen,
        )

    return EvaluationResult(
        triggered=disparadas,
        skipped_cooldown=cooldown,
        skipped_small_drop=pequenas,
        evaluated=len(alertas),
    )


# ----------------------------------------------------------------------- reglas
def _rule_matches(alerta: Alert, snapshot, stats) -> bool:
    if alerta.alert_type == Alert.TYPE_PRICE_BELOW:
        return _price_below(alerta, snapshot)
    if alerta.alert_type == Alert.TYPE_DEAL_DETECTED:
        return is_deal(snapshot.min_price_pen, stats)
    return False


def _price_below(alerta: Alert, snapshot) -> bool:
    if alerta.target_price_pen is None:
        return False
    return Decimal(snapshot.min_price_pen) <= Decimal(alerta.target_price_pen)


def is_deal(price, stats) -> bool:
    """El precio está en el 10% más barato observado en 30 días.

    Sin `DEAL_MIN_SAMPLES` muestras no se opina: un p25 sobre poco histórico
    dispara con cualquier precio normal.
    """
    if stats is None or stats.p25_30d is None:
        return False
    if stats.samples_count < settings.DEAL_MIN_SAMPLES:
        return False

    umbral = Decimal(stats.p25_30d) * settings.DEAL_P25_FACTOR
    return Decimal(price) <= umbral


# --------------------------------------------------------------------- anti-spam
def _antispam_reason(alerta: Alert, price) -> str | None:
    """`None` si se puede avisar; si no, por qué no."""
    ultimo = (
        AlertTrigger.objects.filter(alert=alerta).order_by("-triggered_at").first()
    )
    if ultimo is None:
        return None

    ventana = timezone.now() - timedelta(hours=settings.ALERT_COOLDOWN_HOURS)
    if ultimo.triggered_at > ventana:
        return "cooldown"

    if not _dropped_enough(Decimal(price), Decimal(ultimo.price_pen)):
        return "small_drop"

    return None


def _dropped_enough(nuevo: Decimal, anterior: Decimal) -> bool:
    """El precio bajó al menos ALERT_MIN_DROP_PCT respecto del último aviso."""
    if anterior <= 0:
        return True
    caida = (anterior - nuevo) / anterior * 100
    return caida >= settings.ALERT_MIN_DROP_PCT


# ---------------------------------------------------------------------- disparo
@transaction.atomic
def _create_trigger(alerta: Alert, snapshot) -> AlertTrigger | None:
    try:
        trigger = AlertTrigger.objects.create(
            alert=alerta, snapshot=snapshot, price_pen=snapshot.min_price_pen
        )
    except Exception:  # noqa: BLE001 - una alerta rota no tumba el barrido
        logger.exception("alerts: no se pudo crear el trigger de %s", alerta)
        return None

    from .tasks import send_alert_notification

    transaction.on_commit(
        lambda: send_alert_notification.apply_async(args=[trigger.pk], queue="default")
    )
    return trigger


def _get_stats(route_id: int):
    from apps.flights.models import RouteStats

    return RouteStats.objects.filter(route_id=route_id).first()
