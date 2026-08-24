"""Tasks de mantenimiento: backups y chequeo de salud del sistema."""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .notify import send_admin_alert

logger = logging.getLogger(__name__)


@shared_task(bind=True, acks_late=True, max_retries=2, retry_backoff=True)
def backup_database(self) -> dict:
    """`pg_dump` de Supabase al volumen del VPS.

    El free tier de Supabase no da backups restaurables a demanda, así que
    este archivo es la única copia propia del dato. Ver DEPLOY.md para
    restaurar.
    """
    if not settings.DATABASE_URL:
        logger.warning("backup: sin DATABASE_URL, no hay nada que respaldar")
        return {"status": "skipped", "reason": "no_database_url"}

    destino = Path(settings.BACKUP_DIR)
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("backup: no se pudo crear %s: %s", destino, exc)
        return {"status": "error", "reason": str(exc)}

    marca = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    archivo = destino / f"vueloradar-{marca}.dump"

    comando = [
        settings.PG_DUMP_PATH,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        f"--file={archivo}",
        settings.DATABASE_URL,
    ]

    try:
        proceso = subprocess.run(
            comando, capture_output=True, text=True, timeout=60 * 30, check=False
        )
    except FileNotFoundError:
        logger.error("backup: no se encontró pg_dump (%s)", settings.PG_DUMP_PATH)
        return {"status": "error", "reason": "pg_dump_missing"}
    except subprocess.TimeoutExpired:
        logger.error("backup: pg_dump superó los 30 minutos")
        return {"status": "error", "reason": "timeout"}

    if proceso.returncode != 0:
        logger.error("backup: pg_dump falló (%s): %s", proceso.returncode, proceso.stderr[-500:])
        send_admin_alert(f"VueloRadar: el backup diario falló. {proceso.stderr[-200:]}")
        return {"status": "error", "reason": "pg_dump_failed"}

    tamano = archivo.stat().st_size if archivo.exists() else 0
    borrados = _purge_old_backups(destino)

    logger.info("backup: %s creado (%.1f MB), %d viejos borrados", archivo.name, tamano / 1e6, borrados)
    return {
        "status": "ok",
        "file": archivo.name,
        "bytes": tamano,
        "deleted_old": borrados,
    }


def _purge_old_backups(carpeta: Path) -> int:
    """Borra los dumps más viejos que la retención configurada."""
    corte = timezone.now() - timedelta(days=settings.BACKUP_RETENTION_DAYS)
    borrados = 0

    for archivo in carpeta.glob("vueloradar-*.dump"):
        try:
            modificado = timezone.datetime.fromtimestamp(
                archivo.stat().st_mtime, tz=timezone.get_current_timezone()
            )
            if modificado < corte:
                archivo.unlink()
                borrados += 1
        except OSError as exc:
            logger.warning("backup: no se pudo borrar %s: %s", archivo, exc)

    return borrados


@shared_task(bind=True, acks_late=True, max_retries=1)
def system_healthcheck(self) -> dict:
    """Vigila que el sistema siga vivo y avisa al admin si no.

    Dos síntomas de que algo se rompió en silencio: hace horas que no entra un
    snapshot nuevo, o una fuente lleva demasiado tiempo pausada.
    """
    from apps.flights.models import PriceSnapshot

    from . import ratelimit
    from .tasks import PRIMARY_SOURCE

    problemas = []

    ultimo = PriceSnapshot.objects.order_by("-snapshot_at").first()
    if ultimo is None:
        problemas.append("todavía no hay ningún snapshot en la base")
    else:
        antiguedad = timezone.now() - ultimo.snapshot_at
        horas = antiguedad.total_seconds() / 3600
        if horas > settings.HEALTH_MAX_SNAPSHOT_AGE_HOURS:
            problemas.append(
                f"el último snapshot es de hace {horas:.0f}h "
                f"(máximo tolerado: {settings.HEALTH_MAX_SNAPSHOT_AGE_HOURS}h)"
            )

    if ratelimit.is_paused(PRIMARY_SOURCE):
        pausada_desde = _pause_started_at()
        if pausada_desde is not None and pausada_desde > settings.HEALTH_MAX_PAUSE_HOURS:
            problemas.append(
                f"la fuente {PRIMARY_SOURCE} lleva {pausada_desde:.0f}h pausada"
            )
        else:
            problemas.append(f"la fuente {PRIMARY_SOURCE} está pausada")

    if not problemas:
        logger.info("healthcheck: todo en orden")
        return {"status": "ok", "issues": []}

    detalle = "\n".join(f"- {p}" for p in problemas)
    logger.error("healthcheck: %d problemas detectados\n%s", len(problemas), detalle)
    send_admin_alert(f"VueloRadar: chequeo de salud con problemas.\n{detalle}")

    return {"status": "degraded", "issues": problemas}


def _pause_started_at() -> float | None:
    """Hace cuántas horas empezó la pausa vigente, si se puede saber."""
    from django.core.cache import cache

    from . import ratelimit
    from .tasks import PRIMARY_SOURCE

    try:
        marca = cache.get(f"{ratelimit.pause_key(PRIMARY_SOURCE)}:since")
    except Exception:  # noqa: BLE001
        return None
    if not marca:
        return None

    try:
        inicio = timezone.datetime.fromisoformat(marca)
    except (TypeError, ValueError):
        return None
    return (timezone.now() - inicio).total_seconds() / 3600
