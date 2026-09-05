"""Una sola búsqueda concurrente por usuario.

Sin esto, alguien que manda cinco mensajes seguidos dispara cinco scrapings en
paralelo: consume el cupo, satura el pool y hace cola contra el lock de la
fuente sin que el usuario gane nada.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)


class SingleFlightMiddleware(BaseMiddleware):
    """Descarta el update si el usuario ya tiene una búsqueda en curso."""

    def __init__(self) -> None:
        self._busy: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        if user.id in self._busy:
            logger.info("bot: user=%s update descartado, ya tiene una búsqueda", user.id)
            if isinstance(event, Message):
                await event.answer("⏳ Espera, todavía estoy buscando lo anterior…")
            return None

        self._busy.add(user.id)
        try:
            return await handler(event, data)
        finally:
            self._busy.discard(user.id)
