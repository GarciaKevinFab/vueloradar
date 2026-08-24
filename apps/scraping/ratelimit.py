"""Control de concurrencia y salud por fuente de scraping.

Tres cosas viven acá, todas apoyadas en Redis (vía el cache de Django) para
que funcionen entre workers de Celery:

- **Lock**: una sola consulta concurrente por fuente (CLAUDE.md secc. 7).
- **Contador de fallos consecutivos**: se resetea con cada éxito.
- **Flag de pausa**: a los 3 fallos seguidos la fuente queda castigada 30 min.

En tests el backend es locmem, que soporta las mismas operaciones atómicas
que se usan acá (`add` e `incr`), así que no hace falta fakeredis.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def lock_key(source: str) -> str:
    return f"scraper:{source}:lock"


def failures_key(source: str) -> str:
    return f"scraper:{source}:failures"


def pause_key(source: str) -> str:
    return f"scraper:{source}:paused"


class SourceBusy(RuntimeError):
    """Otra task ya está consultando esta fuente."""


@contextmanager
def source_lock(source: str, *, timeout: int | None = None, blocking: bool = False,
                poll_interval: float = 0.5, max_wait: float = 30.0):
    """Toma el lock de una fuente. Garantiza 1 consulta concurrente.

    El lock expira solo (`timeout`): si un worker muere con el lock tomado, la
    fuente se destraba sola en vez de quedar muerta para siempre.

    Raises:
        SourceBusy: si no se pudo tomar el lock.
    """
    timeout = settings.SOURCE_LOCK_TIMEOUT_SECONDS if timeout is None else timeout
    key = lock_key(source)
    deadline = time.monotonic() + max_wait

    while True:
        # `add` solo escribe si la clave no existe: es el SET NX de Redis.
        if cache.add(key, "1", timeout):
            break
        if not blocking or time.monotonic() >= deadline:
            raise SourceBusy(f"la fuente {source} está ocupada")
        time.sleep(poll_interval)

    try:
        yield
    finally:
        cache.delete(key)


def is_paused(source: str) -> bool:
    """La fuente está castigada tras fallos consecutivos."""
    try:
        return bool(cache.get(pause_key(source)))
    except Exception as exc:  # noqa: BLE001 - Redis caído no bloquea el barrido
        logger.warning("ratelimit: cache inaccesible al leer pausa de %s: %s", source, exc)
        return False


def pause(source: str, seconds: int | None = None) -> int:
    """Castiga la fuente. Devuelve los segundos de pausa aplicados."""
    seconds = settings.SOURCE_PAUSE_SECONDS if seconds is None else seconds
    cache.set(pause_key(source), "1", seconds)
    # El healthcheck necesita saber desde cuándo, no solo que está pausada.
    from django.utils import timezone

    cache.set(f"{pause_key(source)}:since", timezone.now().isoformat(), seconds)
    logger.error("ratelimit: fuente %s pausada %d segundos", source, seconds)
    return seconds


def resume(source: str) -> None:
    cache.delete(pause_key(source))
    cache.delete(f"{pause_key(source)}:since")
    reset_failures(source)


def record_success(source: str) -> None:
    """Un éxito borra el historial de fallos: solo cuentan los consecutivos."""
    reset_failures(source)


def record_failure(source: str) -> int:
    """Suma un fallo consecutivo y devuelve el total acumulado."""
    key = failures_key(source)
    try:
        cache.add(key, 0, None)
        return int(cache.incr(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ratelimit: no se pudo contar el fallo de %s: %s", source, exc)
        return 0


def failure_count(source: str) -> int:
    try:
        return int(cache.get(failures_key(source)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def reset_failures(source: str) -> None:
    try:
        cache.delete(failures_key(source))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ratelimit: no se pudo resetear fallos de %s: %s", source, exc)


def should_pause(source: str) -> bool:
    """True si la fuente llegó al umbral de fallos consecutivos."""
    return failure_count(source) >= settings.SOURCE_MAX_CONSECUTIVE_FAILURES
