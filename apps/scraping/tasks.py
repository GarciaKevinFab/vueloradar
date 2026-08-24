"""Tasks de Celery: barrido automático de rutas e histórico de precios.

Reglas del dominio que se respetan acá (CLAUDE.md secc. 7):
- Una sola consulta concurrente por fuente, vía lock en Redis.
- Delay aleatorio 3-8s entre consultas (lo aplica el propio provider).
- A 3 fallos consecutivos de una fuente: pausa de 30 min y aviso al admin.
"""

from __future__ import annotations

import logging
from datetime import date as Date
from datetime import timedelta

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.flights.models import FlightOffer, PriceSnapshot, Route, RouteStats
from apps.flights.stats import compute_stats

from . import ratelimit
from .notify import send_admin_alert
from .schedule import build_scan_dates
from .services import UnknownAirportError, search_and_store

logger = logging.getLogger(__name__)

#: Fuente que hoy sostiene el barrido. Cuando entren Sky/JetSmart (Fase 5),
#: esto pasa a iterar sobre los providers activos.
PRIMARY_SOURCE = "google_flights"

TASK_KWARGS = {
    "bind": True,
    "acks_late": True,
    "max_retries": 3,
    "retry_backoff": True,
    "retry_backoff_max": 600,
    "retry_jitter": True,
}


@shared_task(**TASK_KWARGS)
def scan_route_date(self, route_id: int, flight_date: str) -> dict:
    """Consulta una ruta para una fecha y guarda el snapshot resultante.

    Devuelve un dict con el resultado en vez de propagar: un fallo de una ruta
    nunca debe tumbar el barrido completo.
    """
    parsed_date = Date.fromisoformat(flight_date)

    try:
        route = Route.objects.select_related("origin", "destination").get(pk=route_id)
    except Route.DoesNotExist:
        logger.error("scan_route_date: la ruta %s ya no existe", route_id)
        return {"status": "skipped", "reason": "route_missing", "route_id": route_id}

    if ratelimit.is_paused(PRIMARY_SOURCE):
        # Reencolar, NO descartar. Saltear vacía la cola en segundos y se pierde
        # el resto del barrido: la pausa dura 30 min y las tasks salteadas se
        # consumen instantáneamente.
        logger.warning(
            "scan_route_date: %s pausada, se reencola %s %s", PRIMARY_SOURCE, route, flight_date
        )
        try:
            raise self.retry(countdown=settings.SOURCE_PAUSE_SECONDS + 30)
        except MaxRetriesExceededError:
            # Pausa que sobrevivió a todos los reintentos: la fuente está rota
            # de verdad. Se abandona esta consulta sin ensuciar el log con un
            # traceback; el próximo barrido la vuelve a intentar.
            logger.error(
                "scan_route_date: %s sigue pausada tras %d reintentos, se abandona %s %s",
                PRIMARY_SOURCE, self.max_retries, route, flight_date,
            )
            return {"status": "skipped", "reason": "source_paused", "route": str(route)}

    try:
        with ratelimit.source_lock(PRIMARY_SOURCE, blocking=True, max_wait=60):
            offers = search_and_store(route.origin_id, route.destination_id, parsed_date)
    except ratelimit.SourceBusy:
        logger.info("scan_route_date: fuente ocupada, reintentando %s %s", route, flight_date)
        raise self.retry(countdown=30)
    except UnknownAirportError as exc:
        logger.error("scan_route_date: %s", exc)
        return {"status": "skipped", "reason": "unknown_airport", "route": str(route)}
    except Exception:
        failures = ratelimit.record_failure(PRIMARY_SOURCE)
        logger.exception(
            "scan_route_date: fallo inesperado en %s %s (fallo consecutivo %d)",
            route, flight_date, failures,
        )
        _maybe_pause_source(failures)
        return {"status": "error", "route": str(route), "flight_date": flight_date}

    # Ojo: el contador de fallos lo maneja el provider, que es el único que
    # distingue "Google falló" de "no hay vuelos ese día". Una lista vacía acá
    # es un dato legítimo, no un síntoma de que la fuente se cayó.
    _maybe_pause_source(ratelimit.failure_count(PRIMARY_SOURCE))

    if not offers:
        logger.info("scan_route_date: 0 ofertas en %s %s", route, flight_date)
        return {"status": "empty", "route": str(route), "flight_date": flight_date}

    snapshot = _store_snapshot(route, parsed_date, offers)
    _evaluate_alerts(snapshot)

    return {
        "status": "ok",
        "route": str(route),
        "flight_date": flight_date,
        "offers": snapshot.offers_count,
        "min_price_pen": str(snapshot.min_price_pen),
    }


@shared_task(**TASK_KWARGS)
def scan_all_monitored(self, priority_max: int | None = None) -> dict:
    """Encola el barrido completo de las rutas monitoreadas.

    Las tasks van una por una a la cola `scraping`, que corre con concurrencia 2.
    No se paraleliza más: el límite es anti-bloqueo, no performance.

    Args:
        priority_max: si se pasa, solo barre rutas con `priority <= priority_max`
            (1 = solo las prioritarias). Útil para barridos parciales manuales.
    """
    routes = Route.objects.filter(is_monitored=True)
    if priority_max is not None:
        routes = routes.filter(priority__lte=priority_max)
    routes = list(routes.order_by("priority", "origin_id", "destination_id"))

    if not routes:
        logger.warning("scan_all_monitored: no hay rutas monitoreadas que barrer")
        return {"routes": 0, "dates": 0, "tasks": 0}

    dates = build_scan_dates(timezone.localdate())
    queued = 0

    for route in routes:
        for flight_date in dates:
            scan_route_date.apply_async(
                args=[route.pk, flight_date.isoformat()], queue="scraping"
            )
            queued += 1

    logger.info(
        "scan_all_monitored: %d rutas x %d fechas = %d consultas encoladas",
        len(routes), len(dates), queued,
    )

    # Las stats se recalculan cuando el barrido ya tuvo tiempo de aterrizar.
    compute_route_stats.apply_async(countdown=_estimated_sweep_seconds(queued))

    return {"routes": len(routes), "dates": len(dates), "tasks": queued}


@shared_task(**TASK_KWARGS)
def compute_route_stats(self, route_id: int | None = None) -> dict:
    """Recalcula `RouteStats` con la ventana de 30 días de snapshots."""
    window_start = timezone.now() - timedelta(days=settings.ROUTE_STATS_WINDOW_DAYS)

    routes = Route.objects.all() if route_id is None else Route.objects.filter(pk=route_id)
    updated = skipped = 0

    for route in routes.iterator():
        prices = list(
            PriceSnapshot.objects.filter(route=route, snapshot_at__gte=window_start)
            .values_list("min_price_pen", flat=True)
        )
        result = compute_stats(prices)
        if result.is_empty:
            skipped += 1
            continue

        RouteStats.objects.update_or_create(
            route=route,
            defaults={
                "avg_30d": result.avg,
                "min_30d": result.minimum,
                "p25_30d": result.p25,
                "median_30d": result.median,
                "samples_count": result.samples,
            },
        )
        updated += 1

    logger.info("compute_route_stats: %d rutas actualizadas, %d sin histórico", updated, skipped)
    return {"updated": updated, "skipped": skipped}


@shared_task(**TASK_KWARGS)
def purge_old_offers(self, days: int | None = None) -> dict:
    """Borra ofertas crudas viejas. Los snapshots NO se tocan nunca.

    Las ofertas individuales pesan y su valor caduca; el resumen de cada
    búsqueda es lo que sostiene el análisis histórico.
    """
    days = settings.OFFER_RETENTION_DAYS if days is None else days
    cutoff = timezone.now() - timedelta(days=days)

    deleted, _ = FlightOffer.objects.filter(scraped_at__lt=cutoff).delete()
    logger.info("purge_old_offers: %d ofertas anteriores a %s borradas", deleted, cutoff.date())
    return {"deleted": deleted, "cutoff": cutoff.date().isoformat()}


@shared_task(bind=True, acks_late=True, max_retries=0)
def pause_source(self, source: str = PRIMARY_SOURCE, seconds: int | None = None) -> dict:
    """Castiga una fuente y avisa al admin por Telegram."""
    applied = ratelimit.pause(source, seconds)
    minutes = round(applied / 60)
    sent = send_admin_alert(
        "VueloRadar: la fuente <code>{}</code> fallo {} veces seguidas. Pausada {} min.".format(
            source, settings.SOURCE_MAX_CONSECUTIVE_FAILURES, minutes
        )
    )
    return {"source": source, "paused_seconds": applied, "admin_notified": sent}


# ------------------------------------------------------------------- internos
def _maybe_pause_source(failures: int) -> None:
    """Dispara la pausa si se alcanzó el umbral de fallos consecutivos."""
    if failures and failures >= settings.SOURCE_MAX_CONSECUTIVE_FAILURES:
        if not ratelimit.is_paused(PRIMARY_SOURCE):
            pause_source.apply_async(args=[PRIMARY_SOURCE])


def _evaluate_alerts(snapshot) -> None:
    """Dispara las alertas que correspondan. Un fallo acá no tumba el barrido."""
    try:
        from apps.alerts.engine import evaluate_snapshot

        evaluate_snapshot(snapshot)
    except Exception:  # noqa: BLE001
        logger.exception("scan_route_date: fallo evaluando alertas de %s", snapshot)


@transaction.atomic
def _store_snapshot(route: Route, flight_date: Date, offers: list) -> PriceSnapshot:
    """Resume las ofertas de una búsqueda en una fila de histórico."""
    prices = [offer.price_pen for offer in offers]
    cheapest = min(offers, key=lambda offer: offer.price_pen)
    result = compute_stats(prices)

    return PriceSnapshot.objects.create(
        route=route,
        flight_date=flight_date,
        min_price_pen=result.minimum,
        avg_price_pen=result.avg,
        offers_count=len(offers),
        cheapest_airline=(cheapest.airline or "")[:100],
    )


def _estimated_sweep_seconds(task_count: int) -> int:
    """Cuánto tarda el barrido, para agendar las stats después.

    Cada consulta cuesta el delay anti-bloqueo (3-8s) más latencia de red. Se
    agrega colchón: llegar tarde no cuesta nada, llegar temprano calcula las
    stats sobre datos incompletos.
    """
    average_delay = (settings.SCRAPE_DELAY_MIN + settings.SCRAPE_DELAY_MAX) / 2
    return int(task_count * (average_delay + 2) * 1.3) + 60
