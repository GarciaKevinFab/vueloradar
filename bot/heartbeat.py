"""Latido del bot para el healthcheck de Docker.

El proceso puede seguir vivo con el polling colgado. Tocar un archivo cada 60s
y mirar su mtime desde fuera distingue "el proceso existe" de "el bot funciona".
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def touch() -> None:
    try:
        archivo = Path(settings.BOT_HEARTBEAT_FILE)
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text(str(asyncio.get_event_loop().time()), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - el latido nunca tumba el bot
        logger.warning("heartbeat: no se pudo escribir el latido: %s", exc)


async def run_forever() -> None:
    """Corre en paralelo al polling hasta que se cancele la task."""
    while True:
        touch()
        await asyncio.sleep(settings.BOT_HEARTBEAT_INTERVAL)
