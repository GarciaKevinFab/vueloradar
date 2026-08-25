"""Handler de texto libre: cualquier mensaje que no sea un comando.

Se registra último, así que solo recibe lo que no matchearon los comandos.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from .. import db, formatting, search_flow, throttle

logger = logging.getLogger(__name__)

router = Router(name="natural")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_free_text(message: Message) -> None:
    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    if not await search_flow.ensure_quota(message, user):
        return
    if not await search_flow.ensure_slot(message):
        return

    pensando = await message.answer("🤔 Entendiendo tu pedido…")
    intent = await db.parse_natural_language(message.text)

    if not intent.is_flight_search:
        throttle.release_slot()
        await pensando.edit_text(formatting.usage_hint_message())
        return

    if not intent.is_complete:
        throttle.release_slot()
        await pensando.edit_text(formatting.missing_fields_message(intent.missing))
        return

    await pensando.delete()

    if intent.is_round_trip:
        await search_flow.run_round_trip(
            message, user, intent.origin, intent.dest, intent.date, intent.return_date
        )
    elif intent.flexible_days > 0:
        await search_flow.run_flexible_search(
            message, user, intent.origin, intent.dest, intent.date, intent.flexible_days
        )
    else:
        await search_flow.run_search(message, user, intent.origin, intent.dest, intent.date)
