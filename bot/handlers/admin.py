"""Comando /stats, solo para el admin."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from django.conf import settings

from .. import db, formatting

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    admin = str(settings.TELEGRAM_ADMIN_CHAT_ID or "").strip()
    return bool(admin) and str(user_id) == admin


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        # Sin acuse de recibo: el comando no debe existir para el resto.
        logger.info("bot: /stats rechazado para user=%s", message.from_user.id)
        return

    metricas = await db.collect_stats()
    await message.answer(formatting.stats_message(metricas))
