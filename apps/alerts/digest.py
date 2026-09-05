"""Resumen semanal de las rutas que cada usuario vigila."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Max, Min
from django.utils import timezone

logger = logging.getLogger(__name__)

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def users_with_alerts():
    """Usuarios con al menos una alerta activa."""
    from apps.users.models import TelegramUser

    return TelegramUser.objects.filter(alerts__is_active=True).distinct()


def build_digest(user) -> str:
    """Mensaje HTML con el rango de precios de la semana, o "" si no hay datos."""
    from apps.alerts.models import Alert
    from apps.flights.models import PriceSnapshot

    desde = timezone.now() - timedelta(days=7)
    rutas = (
        Alert.objects.filter(user=user, is_active=True)
        .select_related("route", "route__origin", "route__destination")
        .values_list("route", flat=True)
        .distinct()
    )

    lineas = []
    for route_id in rutas:
        resumen = (
            PriceSnapshot.objects.filter(route_id=route_id, snapshot_at__gte=desde)
            .aggregate(minimo=Min("min_price_pen"), maximo=Max("min_price_pen"))
        )
        if resumen["minimo"] is None:
            continue

        barato = (
            PriceSnapshot.objects.filter(
                route_id=route_id, snapshot_at__gte=desde, min_price_pen=resumen["minimo"]
            )
            .select_related("route")
            .order_by("snapshot_at")
            .first()
        )
        if barato is None:
            continue

        dia = DIAS[timezone.localtime(barato.snapshot_at).weekday()]
        lineas.append(
            f"• <b>{barato.route.origin_id}→{barato.route.destination_id}</b> se movió entre "
            f"S/ {_money(resumen['minimo'])} y S/ {_money(resumen['maximo'])}, "
            f"mínimo el {dia}."
        )

    if not lineas:
        return ""

    return "\n".join(
        ["📊 <b>Tu semana en VueloRadar</b>", ""]
        + lineas
        + ["", "Con /misalertas revisas lo que estoy vigilando."]
    )


def _money(value) -> str:
    valor = Decimal(value)
    return f"{valor:,.0f}" if valor % 1 == 0 else f"{valor:,.2f}"
