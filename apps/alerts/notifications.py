"""Armado del mensaje de alerta, con veredicto de la IA si está disponible."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

MESES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "set", "oct", "nov", "dic",
]


def build_alert_message(trigger) -> str:
    """Mensaje HTML para Telegram. Nunca lanza: sin IA, se manda igual."""
    alerta = trigger.alert
    snapshot = trigger.snapshot
    ruta = alerta.route

    encabezado = (
        "🚨 <b>¡Oferta detectada!</b>"
        if alerta.alert_type == alerta.TYPE_DEAL_DETECTED
        else "🔔 <b>Bajó de tu precio objetivo</b>"
    )

    lineas = [
        f"{encabezado} {ruta.origin_id} → {ruta.destination_id}",
        f"<b>S/ {_money(trigger.price_pen)}</b>"
        f"{_airline(snapshot)} · vuelo del {_date(snapshot.flight_date)}",
    ]

    contexto = _price_context(trigger.price_pen, _stats(ruta))
    if contexto:
        lineas.append(contexto)

    veredicto = _verdict_line(ruta, snapshot.flight_date, trigger.price_pen)
    if veredicto:
        lineas.append(veredicto)

    lineas.append(_deep_link(snapshot))
    return "\n".join(l for l in lineas if l)


def _verdict_line(route, flight_date, price) -> str:
    """La línea 🤖. Se omite entera si la IA no pudo opinar."""
    try:
        from apps.ai_analyst.analyst import get_verdict

        verdict = get_verdict(route, flight_date, price)
    except Exception:  # noqa: BLE001 - la alerta se manda igual
        logger.exception("alerts: el analista falló, se envía sin veredicto")
        return ""

    if verdict is None:
        return ""

    return f"🤖 <b>Veredicto: {verdict.label}.</b> {verdict.reason}"


def _price_context(price, stats) -> str:
    if stats is None or not stats.avg_30d:
        return ""

    avg = Decimal(stats.avg_30d)
    if avg <= 0:
        return ""

    pct = int(((avg - Decimal(price)) / avg * 100).quantize(Decimal("1")))
    if pct <= 0:
        return ""
    return f"Está <b>{pct}% bajo</b> el promedio del último mes (S/ {_money(avg)})."


def _deep_link(snapshot) -> str:
    from apps.flights.models import FlightOffer

    oferta = (
        FlightOffer.objects.filter(
            route_id=snapshot.route_id, search_date=snapshot.flight_date
        )
        .exclude(deep_link="")
        .order_by("price_pen")
        .first()
    )
    if oferta is None:
        return ""
    return f'🔗 <a href="{oferta.deep_link}">Ver en Google Flights</a>'


def _stats(route):
    from apps.flights.models import RouteStats

    return RouteStats.objects.filter(route=route).first()


def _airline(snapshot) -> str:
    return f" · {snapshot.cheapest_airline}" if snapshot.cheapest_airline else ""


def _date(value) -> str:
    return f"{value.day} {MESES[value.month - 1]}"


def _money(value) -> str:
    valor = Decimal(value)
    return f"{valor:,.0f}" if valor % 1 == 0 else f"{valor:,.2f}"
