"""Log de cada update: quién y qué comando, nunca el contenido del mensaje."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = getattr(getattr(event, "from_user", None), "id", "?")
        etiqueta = _label(event)

        inicio = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            elapsed = time.monotonic() - inicio
            logger.info("bot: user=%s %s (%.2fs)", user_id, etiqueta, elapsed)


def _label(event: TelegramObject) -> str:
    """Comando si lo hay; si no, solo el tipo. Nunca el texto del usuario."""
    if isinstance(event, Message) and event.text:
        if event.text.startswith("/"):
            return event.text.split()[0].split("@")[0]
        return "texto-libre"
    return type(event).__name__.lower()
