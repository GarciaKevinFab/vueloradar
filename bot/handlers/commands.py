"""Handlers de comandos explícitos."""

from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from django.conf import settings
from django.utils import timezone

from .. import db, formatting, search_flow

logger = logging.getLogger(__name__)

router = Router(name="commands")


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep_link(message: Message, command: CommandObject) -> None:
    """`/start LIM-CUZ`, desde el botón de una ficha de ruta en la web.

    Si el payload no resuelve a una ruta conocida caemos en la bienvenida
    normal: el usuario igual entra, solo pierde el atajo.
    """
    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    route = await db.get_route_from_deep_link(command.args or "")
    if route is None:
        logger.info("start: payload no reconocido %r", command.args)
        await message.answer(formatting.welcome_message(user.first_name))
        return

    logger.info("start: llegada desde la web por %s", route.code)
    await message.answer(formatting.welcome_from_route(user.first_name, route))


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    await message.answer(formatting.welcome_message(user.first_name))


@router.message(Command("ayuda", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(formatting.help_message(settings.FREE_DAILY_SEARCHES))


@router.message(Command("vuelo"))
async def cmd_flight(message: Message, command: CommandObject) -> None:
    """/vuelo LIM CUZ 2026-09-15"""
    args = (command.args or "").split()
    if len(args) != 3:
        await message.answer(
            "Formato: <code>/vuelo ORIGEN DESTINO FECHA</code>\n"
            "Ejemplo: <code>/vuelo LIM CUZ 2026-09-15</code>\n\n"
            "También podés escribirme en lenguaje natural: "
            "<i>«vuelo de Lima a Cusco el 15 de setiembre»</i>"
        )
        return

    origin, dest, fecha_texto = args[0].upper(), args[1].upper(), args[2]

    fecha = _parse_date(fecha_texto)
    if fecha is None:
        await message.answer(
            f"No entendí la fecha <code>{formatting.escape(fecha_texto)}</code>. "
            f"Usá el formato <code>AAAA-MM-DD</code>, por ejemplo <code>2026-09-15</code>."
        )
        return

    if fecha < timezone.localdate():
        await message.answer("Esa fecha ya pasó. Elegí una a futuro.")
        return

    if origin == dest:
        await message.answer("El origen y el destino son el mismo aeropuerto 🙂")
        return

    for code in (origin, dest):
        if not await db.airport_exists(code):
            await message.answer(
                f"No conozco el aeropuerto <code>{formatting.escape(code)}</code>.\n"
                f"Mirá los códigos disponibles con /rutas."
            )
            return

    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    if not await search_flow.ensure_quota(message, user):
        return
    if not await search_flow.ensure_slot(message):
        return

    await search_flow.run_search(message, user, origin, dest, fecha)


@router.message(Command("rutas"))
async def cmd_routes(message: Message) -> None:
    filas = await db.list_monitored_routes()
    await message.answer(formatting.format_routes(filas))


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
