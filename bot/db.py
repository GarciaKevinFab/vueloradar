"""Puente entre el bot (async) y Django (sync).

aiogram corre sobre asyncio y el ORM de Django es síncrono. Todo acceso a la
base pasa por acá envuelto en `sync_to_async`, para no bloquear el event loop
ni disparar `SynchronousOnlyOperation`.

El scraping, que además de bloqueante tarda segundos, va a un thread pool
propio para no consumir los hilos que Django reserva.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from asgiref.sync import sync_to_async
from django.conf import settings

logger = logging.getLogger(__name__)

_SEARCH_POOL = ThreadPoolExecutor(
    max_workers=settings.BOT_SEARCH_WORKERS, thread_name_prefix="vueloradar-bot"
)


# ------------------------------------------------------------------- usuarios
@sync_to_async
def get_or_create_user(telegram_id: int, *, username: str = "", first_name: str = ""):
    from apps.users.services import get_or_create_user as _get

    return _get(telegram_id, username=username, first_name=first_name)


@sync_to_async
def check_quota(user):
    from apps.users.services import check_quota as _check

    return _check(user)


@sync_to_async
def consume_search(user):
    from apps.users.services import consume_search as _consume

    return _consume(user)


# -------------------------------------------------------------------- vuelos
async def search_flights(origin: str, dest: str, flight_date: date) -> list:
    """Corre la búsqueda en un thread aparte: bloquea segundos por el scraping."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_SEARCH_POOL, _search_sync, origin, dest, flight_date)


def _search_sync(origin: str, dest: str, flight_date: date) -> list:
    from django.db import close_old_connections

    from apps.scraping.services import search_and_store

    close_old_connections()
    try:
        return search_and_store(origin, dest, flight_date)
    except Exception:  # noqa: BLE001 - el bot responde, no se cae
        logger.exception("bot: búsqueda fallida %s->%s %s", origin, dest, flight_date)
        return []
    finally:
        close_old_connections()


@sync_to_async
def get_route_stats(origin: str, dest: str):
    """`RouteStats` de la ruta, o None si todavía no hay histórico."""
    from apps.flights.models import RouteStats

    return (
        RouteStats.objects.filter(
            route__origin_id=origin, route__destination_id=dest
        ).first()
    )


@sync_to_async
def list_monitored_routes(limit: int = 25) -> list:
    """Rutas monitoreadas con su mínimo de 30 días, las más baratas primero."""
    from apps.flights.models import Route

    rutas = (
        Route.objects.filter(is_monitored=True)
        .select_related("origin", "destination", "stats")
        .order_by("priority", "origin_id", "destination_id")[:limit]
    )

    filas = []
    for ruta in rutas:
        stats = getattr(ruta, "stats", None)
        filas.append(
            {
                "origin": ruta.origin_id,
                "dest": ruta.destination_id,
                "origin_city": ruta.origin.city,
                "dest_city": ruta.destination.city,
                "min_price": stats.min_30d if stats else None,
            }
        )
    return filas


@sync_to_async
def airport_exists(iata: str) -> bool:
    from apps.flights.models import Airport

    return Airport.objects.filter(pk=iata.upper(), is_active=True).exists()


# ------------------------------------------------------------------------ IA
@sync_to_async
def parse_natural_language(message: str):
    """El parser hace red y toca la base; va fuera del event loop igual."""
    from apps.ai_analyst.nl_parser import parse_flight_request

    return parse_flight_request(message)


# ------------------------------------------------------------------- alertas
@sync_to_async
def create_alert(user, origin: str, dest: str, target_price=None, flight_date=None) -> dict:
    """Alta de alerta. Devuelve un dict plano para que el handler no toque el ORM."""
    from apps.alerts.services import AlertLimitReached, create_alert as _create, get_quota
    from apps.flights.models import Route

    route = Route.objects.filter(origin_id=origin, destination_id=dest).first()
    if route is None:
        return {"status": "unknown_route"}

    try:
        _alerta, creada = _create(
            user, route, target_price=target_price, flight_date=flight_date
        )
    except AlertLimitReached as exc:
        return {"status": "limit_reached", "limit": exc.limit}

    return {
        "status": "ok",
        "created": creada,
        "remaining": get_quota(user).remaining,
    }


@sync_to_async
def list_alerts(user) -> list:
    """Alertas activas, ya serializadas: el handler no debe tocar objetos del ORM."""
    from apps.alerts.services import list_alerts as _list

    return [{"id": a.pk, "describe": a.describe} for a in _list(user)]


@sync_to_async
def deactivate_alert(user, alert_id: int):
    from apps.alerts.services import deactivate

    alerta = deactivate(user, alert_id)
    return alerta.pk if alerta else None


@sync_to_async
def get_verdict(origin: str, dest: str, flight_date, price):
    """Veredicto del analista, o None si no hay histórico o falló la IA."""
    from apps.ai_analyst.analyst import get_verdict as _verdict
    from apps.flights.models import Route

    route = (
        Route.objects.select_related("origin", "destination")
        .filter(origin_id=origin, destination_id=dest)
        .first()
    )
    if route is None:
        return None
    return _verdict(route, flight_date, price)


# ------------------------------------------------------------------- métricas
@sync_to_async
def collect_stats() -> dict:
    """Fotografía del sistema para el /stats del admin."""
    from datetime import timedelta

    from django.db.models import Sum
    from django.utils import timezone

    from apps.ai_analyst.models import AIUsageLog
    from apps.alerts.models import Alert, AlertTrigger
    from apps.flights.models import PriceSnapshot
    from apps.scraping import ratelimit
    from apps.scraping.tasks import PRIMARY_SOURCE
    from apps.users.models import TelegramUser

    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
    hace_24h = timezone.now() - timedelta(hours=24)

    consumo = AIUsageLog.objects.filter(date__gte=inicio_mes).values("provider").annotate(
        llamadas=Sum("calls"), entrada=Sum("input_tokens"), salida=Sum("output_tokens")
    )

    ultimo = PriceSnapshot.objects.order_by("-snapshot_at").first()

    return {
        "usuarios_total": TelegramUser.objects.count(),
        "usuarios_activos_hoy": TelegramUser.objects.filter(last_active_at__date=hoy).count(),
        "premium": TelegramUser.objects.filter(plan=TelegramUser.PLAN_PREMIUM).count(),
        "busquedas_hoy": TelegramUser.objects.filter(searches_reset_date=hoy).aggregate(
            n=Sum("searches_today")
        )["n"] or 0,
        "snapshots_hoy": PriceSnapshot.objects.filter(snapshot_at__date=hoy).count(),
        "snapshots_total": PriceSnapshot.objects.count(),
        "ultimo_snapshot": ultimo.snapshot_at if ultimo else None,
        "alertas_activas": Alert.objects.filter(is_active=True).count(),
        "alertas_disparadas_24h": AlertTrigger.objects.filter(triggered_at__gte=hace_24h).count(),
        "fuente_pausada": ratelimit.is_paused(PRIMARY_SOURCE),
        "fallos_fuente": ratelimit.failure_count(PRIMARY_SOURCE),
        "ia_mes": list(consumo),
    }
