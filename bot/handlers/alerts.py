"""Handlers de alertas: alta, listado y baja con botones inline."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import db, formatting

logger = logging.getLogger(__name__)

router = Router(name="alerts")

DEACTIVATE_PREFIX = "alert:off:"


@router.message(Command("alerta"))
async def cmd_alert(message: Message, command: CommandObject) -> None:
    """/alerta LIM CUZ [precio] [fecha]"""
    args = (command.args or "").split()

    if len(args) < 2:
        await message.answer(
            "Para crear una alerta necesito la ruta:\n"
            "<code>/alerta LIM CUZ</code> — te aviso de cualquier oferta\n"
            "<code>/alerta LIM CUZ 180</code> — te aviso si baja de S/ 180\n"
            "<code>/alerta LIM CUZ 180 2026-10-14</code> — solo esa fecha\n\n"
            "Con /misalertas ves las que tenés activas."
        )
        return

    origin, dest = args[0].upper(), args[1].upper()

    precio = None
    if len(args) >= 3:
        precio = _parse_price(args[2])
        if precio is None:
            await message.answer(
                f"No entendí el precio <code>{formatting.escape(args[2])}</code>. "
                f"Poné solo el número, por ejemplo <code>180</code>."
            )
            return

    fecha = None
    if len(args) >= 4:
        fecha = _parse_date(args[3])
        if fecha is None:
            await message.answer(
                f"No entendí la fecha <code>{formatting.escape(args[3])}</code>. "
                f"Usá <code>AAAA-MM-DD</code>."
            )
            return

    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )

    resultado = await db.create_alert(user, origin, dest, precio, fecha)

    if resultado["status"] == "unknown_route":
        await message.answer(
            f"No conozco la ruta <code>{formatting.escape(origin)}→{formatting.escape(dest)}</code>. "
            f"Mirá los códigos con /rutas."
        )
        return

    if resultado["status"] == "limit_reached":
        await message.answer(formatting.alert_limit_message(resultado["limit"]))
        return

    await message.answer(
        formatting.alert_created_message(
            origin, dest, precio, fecha,
            created=resultado["created"],
            remaining=resultado["remaining"],
        )
    )


@router.message(Command("misalertas"))
async def cmd_my_alerts(message: Message) -> None:
    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    alertas = await db.list_alerts(user)

    if not alertas:
        await message.answer(formatting.no_alerts_message())
        return

    await message.answer(
        formatting.alerts_list_message(alertas),
        reply_markup=_deactivate_keyboard(alertas),
    )


@router.callback_query(F.data.startswith(DEACTIVATE_PREFIX))
async def on_deactivate(callback: CallbackQuery) -> None:
    try:
        alert_id = int(callback.data.removeprefix(DEACTIVATE_PREFIX))
    except ValueError:
        await callback.answer("Botón inválido")
        return

    user = await db.get_or_create_user(
        callback.from_user.id,
        username=callback.from_user.username or "",
        first_name=callback.from_user.first_name or "",
    )
    desactivada = await db.deactivate_alert(user, alert_id)

    if desactivada is None:
        await callback.answer("Esa alerta ya no está activa")
    else:
        await callback.answer("Alerta desactivada ✅")

    alertas = await db.list_alerts(user)
    if alertas:
        await callback.message.edit_text(
            formatting.alerts_list_message(alertas),
            reply_markup=_deactivate_keyboard(alertas),
        )
    else:
        await callback.message.edit_text(formatting.no_alerts_message())


def _deactivate_keyboard(alertas: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🗑 {a['describe']}",
                    callback_data=f"{DEACTIVATE_PREFIX}{a['id']}",
                )
            ]
            for a in alertas
        ]
    )


def _parse_price(raw: str) -> Decimal | None:
    try:
        precio = Decimal(raw.replace(",", "").replace("S/", "").strip())
    except (InvalidOperation, AttributeError):
        return None
    return precio if precio > 0 else None


def _parse_date(raw: str):
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
