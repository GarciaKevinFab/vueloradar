"""Venta de premium con Telegram Stars.

El flujo que exige Telegram es de cuatro pasos y no se puede saltar ninguno:

    sendInvoice → pre_checkout_query → answerPreCheckoutQuery → successful_payment

**`answerPreCheckoutQuery` tiene 10 segundos.** Pasado ese plazo Telegram
cancela la transacción y el usuario ve un error sin entender por qué. Por eso
ahí no se toca la base de datos: se valida contra la tabla de planes, que está
en memoria, y recién en `successful_payment` se acredita.

Con estrellas no hay pasarela ni `provider_token`: va vacío. Nunca vemos un
medio de pago, así que este archivo no maneja ningún dato sensible.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apps.users.payments import PLANES, plan_por_clave

from .. import db, formatting

logger = logging.getLogger(__name__)
router = Router(name="premium")

#: Prefijo del `callback_data` de los botones de compra.
COMPRAR = "comprar:"


def _teclado_de_planes():
    """Un botón por paquete."""
    kb = InlineKeyboardBuilder()
    for plan in PLANES.values():
        kb.button(text=f"{plan.titulo} · {plan.estrellas} ⭐",
                  callback_data=f"{COMPRAR}{plan.clave}")
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("premium"))
async def cmd_premium(message: Message) -> None:
    """Muestra qué da el premium y cuánto cuesta."""
    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    estado = await db.estado_premium(user)

    await message.answer(
        formatting.premium_offer(estado),
        reply_markup=_teclado_de_planes(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith(COMPRAR))
async def enviar_factura(callback: CallbackQuery) -> None:
    """Manda la factura del paquete elegido."""
    plan = plan_por_clave(callback.data.removeprefix(COMPRAR))
    if plan is None:
        # Un callback viejo tras cambiar los planes: se avisa, no se revienta.
        await callback.answer("Ese plan ya no está disponible.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer_invoice(
        title=plan.titulo,
        description=plan.descripcion,
        # El payload vuelve intacto en `successful_payment`: es como sabemos
        # qué compró sin guardar estado entre los dos mensajes.
        payload=f"premium:{plan.clave}",
        # Vacío a propósito: los bienes digitales van por estrellas y no hay
        # proveedor de pago de por medio.
        provider_token="",
        currency="XTR",
        # Con estrellas la lista lleva exactamente un ítem.
        prices=[LabeledPrice(label=plan.titulo, amount=plan.estrellas)],
    )


@router.pre_checkout_query()
async def confirmar_checkout(query: PreCheckoutQuery) -> None:
    """Última chance de rechazar el cobro. Hay 10 segundos.

    No se consulta la base acá a propósito: una consulta lenta agotaría el
    plazo y Telegram cancelaría un pago perfectamente válido. Lo único que hay
    que verificar es que el plan siga existiendo, y eso está en memoria.
    """
    clave = query.invoice_payload.removeprefix("premium:")
    if plan_por_clave(clave) is None:
        await query.answer(ok=False, error_message="Ese plan ya no está disponible.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def pago_recibido(message: Message) -> None:
    """El pago entró: se acredita y se avisa."""
    pago = message.successful_payment
    plan = plan_por_clave(pago.invoice_payload.removeprefix("premium:"))
    if plan is None:
        # No deberia pasar (el checkout ya valido), pero si el plan se elimino
        # entre medio el dinero ya entro: se avisa en vez de callar.
        logger.error(
            "premium: pago %s sin plan conocido (payload=%s)",
            pago.telegram_payment_charge_id, pago.invoice_payload,
        )
        await message.answer(formatting.premium_error())
        return

    user = await db.get_or_create_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    acreditacion = await db.acreditar_pago(
        user,
        charge_id=pago.telegram_payment_charge_id,
        estrellas=pago.total_amount,
        dias=plan.dias,
    )

    logger.info(
        "premium: %s pago %s estrellas, premium hasta %s (repetido=%s)",
        message.from_user.id, pago.total_amount,
        acreditacion.hasta, acreditacion.ya_estaba_acreditado,
    )
    await message.answer(formatting.premium_gracias(acreditacion))
