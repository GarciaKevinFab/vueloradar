"""Alta, baja y listado de alertas. Todo síncrono; el bot lo envuelve."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings

from .models import Alert

logger = logging.getLogger(__name__)


class AlertLimitReached(Exception):
    """El usuario llegó al tope de alertas activas de su plan."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"límite de {limit} alertas activas alcanzado")


@dataclass(frozen=True)
class AlertQuota:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def is_full(self) -> bool:
        return self.remaining <= 0


def max_alerts_for(user) -> int:
    return settings.PREMIUM_MAX_ALERTS if user.is_premium else settings.FREE_MAX_ALERTS


def get_quota(user) -> AlertQuota:
    usadas = Alert.objects.filter(user=user, is_active=True).count()
    return AlertQuota(used=usadas, limit=max_alerts_for(user))


def create_alert(
    user,
    route,
    *,
    target_price: Decimal | None = None,
    flight_date: date | None = None,
) -> tuple[Alert, bool]:
    """Crea (o reactiva) una alerta. Devuelve `(alerta, fue_creada)`.

    Si ya existe una igual, la reactiva en vez de duplicarla — así el usuario
    no gasta cupo repitiendo el mismo comando.

    Raises:
        AlertLimitReached: si el plan no da para más alertas activas.
    """
    tipo = Alert.TYPE_PRICE_BELOW if target_price is not None else Alert.TYPE_DEAL_DETECTED

    existente = Alert.objects.filter(
        user=user, route=route, alert_type=tipo, flight_date=flight_date
    ).first()

    if existente is not None:
        cambios = []
        if not existente.is_active:
            quota = get_quota(user)
            if quota.is_full:
                raise AlertLimitReached(quota.limit)
            existente.is_active = True
            cambios.append("is_active")
        if target_price is not None and existente.target_price_pen != target_price:
            existente.target_price_pen = target_price
            cambios.append("target_price_pen")
        if cambios:
            existente.save(update_fields=cambios)
        return existente, False

    quota = get_quota(user)
    if quota.is_full:
        raise AlertLimitReached(quota.limit)

    alerta = Alert.objects.create(
        user=user,
        route=route,
        alert_type=tipo,
        target_price_pen=target_price,
        flight_date=flight_date,
    )
    logger.info("alerts: %s creó alerta %s", user.telegram_id, alerta)
    return alerta, True


def list_alerts(user) -> list[Alert]:
    return list(
        Alert.objects.filter(user=user, is_active=True)
        .select_related("route", "route__origin", "route__destination")
        .order_by("-created_at")
    )


def deactivate(user, alert_id: int) -> Alert | None:
    """Desactiva una alerta del usuario. `None` si no es suya o no existe."""
    alerta = Alert.objects.filter(pk=alert_id, user=user, is_active=True).first()
    if alerta is None:
        return None
    alerta.is_active = False
    alerta.save(update_fields=["is_active"])
    logger.info("alerts: %s desactivó la alerta %s", user.telegram_id, alert_id)
    return alerta
