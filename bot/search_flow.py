"""Flujo de búsqueda del bot, compartido por los handlers.

Un solo lugar decide: valida cupo, avisa que está buscando, ejecuta fuera del
event loop, edita el mensaje con el resultado y descuenta la búsqueda.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from django.conf import settings

from . import db, formatting, throttle

logger = logging.getLogger(__name__)

#: Los mensajes de Telegram separan bloques con una línea en blanco.
SEPARADOR = chr(10) * 2


async def run_search(message, user, origin: str, dest: str, flight_date: date) -> None:
    """Búsqueda de un solo día. Edita el mensaje de 'buscando' con el resultado."""
    try:
        await _run_search(message, user, origin, dest, flight_date)
    finally:
        throttle.release_slot()


async def _run_search(message, user, origin: str, dest: str, flight_date: date) -> None:
    aviso = await message.answer(formatting.searching_message(origin, dest, flight_date))

    offers = await db.search_flights(origin, dest, flight_date)

    if offers is None:
        # Falla nuestra, no ausencia de vuelos. Decirlo tal cual evita que el
        # usuario descarte una ruta que sí existe, y no le consume una búsqueda.
        await aviso.edit_text(formatting.system_error_message())
        return

    stats = await db.get_route_stats(origin, dest) if offers else None

    texto = formatting.format_results(
        origin=origin,
        dest=dest,
        flight_date=flight_date,
        offers=offers,
        stats=stats,
        limit=settings.BOT_RESULTS_LIMIT,
    )
    await aviso.edit_text(texto)

    # Solo se cobra si la búsqueda llegó a ejecutarse.
    await db.consume_search(user)

    # El veredicto va después, en una segunda edición: pedirlo antes retrasaría
    # los vuelos varios segundos por una línea que puede no llegar.
    await _append_verdict(aviso, texto, origin, dest, flight_date, offers)


async def _append_verdict(mensaje, texto: str, origin, dest, flight_date, offers) -> None:
    """Agrega la línea del analista si la IA pudo opinar. Silencioso si no."""
    if not offers:
        return

    try:
        verdict = await db.get_verdict(origin, dest, flight_date, offers[0].price_pen)
    except Exception:  # noqa: BLE001 - los vuelos ya se mostraron
        logger.exception("bot: fallo pidiendo el veredicto")
        return

    linea = formatting.verdict_line(verdict)
    if not linea:
        return

    try:
        await mensaje.edit_text(SEPARADOR.join([texto, linea]))
    except Exception as exc:  # noqa: BLE001 - editar puede fallar por rate limit
        logger.warning("bot: no se pudo agregar el veredicto: %s", exc)


async def run_flexible_search(
    message, user, origin: str, dest: str, target: date, flexible_days: int
) -> None:
    """Barre la fecha objetivo +/- N días y muestra el mejor precio de cada uno."""
    try:
        await _run_flexible_search(message, user, origin, dest, target, flexible_days)
    finally:
        throttle.release_slot()


async def _run_flexible_search(
    message, user, origin: str, dest: str, target: date, flexible_days: int
) -> None:
    flexible_days = min(flexible_days, settings.BOT_MAX_FLEXIBLE_DAYS)
    fechas = _date_window(target, flexible_days)

    aviso = await message.answer(
        f"📅 Buscando <b>{formatting.escape(origin)} → {formatting.escape(dest)}</b> "
        f"en {len(fechas)} fechas alrededor del {formatting.format_date(target)}…\n"
        f"<i>Esto tarda un poco más.</i>"
    )

    resultados = await asyncio.gather(
        *(db.search_flights(origin, dest, fecha) for fecha in fechas)
    )

    if all(o is None for o in resultados):
        await aviso.edit_text(formatting.system_error_message())
        return

    mejor_por_dia = {}
    for fecha, offers in zip(fechas, resultados):
        if offers:
            mejor_por_dia[fecha] = min(offer.price_pen for offer in offers)

    await aviso.edit_text(
        formatting.format_flexible_results(
            origin=origin, dest=dest, best_by_day=mejor_por_dia, target=target
        )
    )
    await db.consume_search(user)


def _date_window(target: date, flexible_days: int) -> list[date]:
    """Fechas a consultar, sin salirse del pasado."""
    from django.utils import timezone

    hoy = timezone.localdate()
    fechas = [
        target + timedelta(days=delta)
        for delta in range(-flexible_days, flexible_days + 1)
    ]
    return [f for f in fechas if f >= hoy]


async def ensure_slot(message) -> bool:
    """Toma un slot del techo global. Avisa y corta si el sistema está lleno."""
    try:
        throttle._acquire_or_raise()
    except throttle.NoSlotAvailable:
        await message.answer(formatting.busy_message())
        logger.info("bot: búsqueda rechazada, techo global alcanzado")
        return False
    return True


async def ensure_quota(message, user) -> bool:
    """Corta la búsqueda si el usuario agotó su cupo. Devuelve si puede seguir."""
    quota = await db.check_quota(user)
    if quota.allowed:
        return True

    await message.answer(formatting.quota_exceeded_message(quota.limit))
    logger.info("bot: usuario %s bloqueado por cupo", user.telegram_id)
    return False
