"""Techo global de búsquedas simultáneas en todo el sistema.

El anti-flood por usuario (middleware) evita que una persona dispare cinco
scrapings; esto evita que veinte personas distintas lo hagan. El contador vive
en Redis para que valga entre procesos (bot, workers) y no solo dentro de uno.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

COUNTER_KEY = "bot:searches:inflight"


class NoSlotAvailable(RuntimeError):
    """El sistema está en su techo de búsquedas simultáneas."""


@contextmanager
def search_slot():
    """Toma un slot del semáforo global, o lanza `NoSlotAvailable`.

    El contador tiene TTL: si un proceso muere con un slot tomado, el sistema
    se destraba solo en vez de quedar bloqueado para siempre.
    """
    if not _acquire():
        raise NoSlotAvailable("no hay slots libres")
    try:
        yield
    finally:
        _release()


def _acquire_or_raise() -> None:
    """Toma un slot o lanza. Lo libera `release_slot` cuando termina la búsqueda."""
    if not _acquire():
        raise NoSlotAvailable("no hay slots libres")


def release_slot() -> None:
    """Devuelve un slot tomado con `_acquire_or_raise`."""
    _release()


def in_flight() -> int:
    try:
        return int(cache.get(COUNTER_KEY) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _acquire() -> bool:
    try:
        cache.add(COUNTER_KEY, 0, settings.BOT_GLOBAL_SLOT_TTL)
        actual = int(cache.incr(COUNTER_KEY))
    except Exception as exc:  # noqa: BLE001 - Redis caído no bloquea el bot
        logger.warning("throttle: contador inaccesible, se deja pasar: %s", exc)
        return True

    if actual > settings.BOT_GLOBAL_SEARCH_LIMIT:
        _release()
        logger.info("throttle: techo global alcanzado (%d)", settings.BOT_GLOBAL_SEARCH_LIMIT)
        return False

    return True


def _release() -> None:
    try:
        if int(cache.get(COUNTER_KEY) or 0) > 0:
            cache.decr(COUNTER_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("throttle: no se pudo liberar el slot: %s", exc)


def reset() -> None:
    """Solo para tests y para destrabar a mano."""
    try:
        cache.delete(COUNTER_KEY)
    except Exception:  # noqa: BLE001
        pass
