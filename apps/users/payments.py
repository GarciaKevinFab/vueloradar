"""Premium pagado con Telegram Stars.

Telegram cobra los bienes digitales **solo** en estrellas (`XTR`): no se puede
poner otra moneda ni otro proveedor dentro de la app, porque las tiendas de
Apple y Google no lo permiten. Eso simplifica todo — no hay pasarela, no hay
datos de tarjeta y nosotros nunca vemos un medio de pago.

Lo único que hay que guardar es el `telegram_payment_charge_id`: es lo que
permite reembolsar más tarde, y Telegram no lo vuelve a entregar.

Todas las funciones son síncronas; el bot las envuelve en `sync_to_async`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import StarPayment, TelegramUser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Plan:
    """Un paquete de premium a la venta."""

    clave: str
    dias: int
    estrellas: int
    titulo: str
    descripcion: str

    @property
    def por_mes(self) -> float:
        """Estrellas por cada 30 días, para poder comparar los paquetes."""
        return round(self.estrellas * 30 / self.dias, 1)


#: Los tres paquetes. El precio va en estrellas, que es la unidad que ve el
#: usuario; Telegram las vende a ~US$ 0,015, así que 150 ⭐ son unos US$ 2.
#: El trimestral y el anual bajan el precio por mes a propósito: el producto
#: mejora con el tiempo (cuanto más histórico, mejores señales), y conviene que
#: la gente se quede en vez de tener que renovar a mano cada 30 días.
PLANES: dict[str, Plan] = {
    "mes": Plan(
        clave="mes", dias=30, estrellas=150,
        titulo="Premium · 1 mes",
        descripcion="Alertas y búsquedas sin límite durante 30 días.",
    ),
    "trimestre": Plan(
        clave="trimestre", dias=90, estrellas=390,
        titulo="Premium · 3 meses",
        descripcion="Tres meses seguidos. Sale 13% menos por mes.",
    ),
    "anio": Plan(
        clave="anio", dias=365, estrellas=1290,
        titulo="Premium · 1 año",
        descripcion="Un año completo. Sale 29% menos por mes.",
    ),
}


def plan_por_clave(clave: str) -> Plan | None:
    return PLANES.get(clave)


@dataclass(frozen=True)
class Acreditacion:
    """Cómo quedó el usuario después de pagar."""

    user: TelegramUser
    dias: int
    hasta: date | None
    ya_estaba_acreditado: bool


def acreditar_pago(user: TelegramUser, *, charge_id: str, estrellas: int, dias: int) -> Acreditacion:
    """Acredita el pago y extiende el premium.

    **Idempotente por `charge_id`.** Telegram puede reenviar el mismo update si
    no recibe confirmación, y regalarle el doble de días a alguien sería un
    error caro de descubrir. La unicidad la garantiza la base y no un `exists()`
    previo: entre la consulta y el insert hay una ventana en la que dos updates
    simultáneos pasarían los dos.

    El premium **se extiende, no se reemplaza**: si a alguien le quedan 10 días
    y compra un mes, termina con 40. Pisarle la fecha sería quitarle lo que ya
    había pagado.
    """
    hoy = timezone.localdate()

    try:
        with transaction.atomic():
            StarPayment.objects.create(
                user=user, charge_id=charge_id, stars=estrellas, days=dias
            )
    except IntegrityError:
        # Ya estaba acreditado: se devuelve el estado actual sin sumar nada.
        actual = TelegramUser.objects.get(pk=user.pk)
        logger.info("payments: charge %s repetido, no se acredita de nuevo", charge_id)
        return Acreditacion(
            user=actual, dias=dias,
            hasta=actual.premium_until, ya_estaba_acreditado=True,
        )

    with transaction.atomic():
        bloqueado = TelegramUser.objects.select_for_update().get(pk=user.pk)
        # Vencido (o nunca tuvo) cuenta desde hoy; vigente, se le suma.
        desde = bloqueado.premium_until if (
            bloqueado.premium_until and bloqueado.premium_until > hoy
        ) else hoy
        bloqueado.premium_until = desde + timedelta(days=dias)
        bloqueado.plan = TelegramUser.PLAN_PREMIUM
        bloqueado.save(update_fields=["plan", "premium_until"])

    logger.info(
        "payments: %s estrellas de %s -> premium hasta %s",
        estrellas, user.telegram_id, bloqueado.premium_until,
    )
    return Acreditacion(
        user=bloqueado, dias=dias,
        hasta=bloqueado.premium_until, ya_estaba_acreditado=False,
    )


def estado_premium(user: TelegramUser) -> dict:
    """Resumen para mostrarle al usuario en qué situación está."""
    vigente = user.is_premium
    return {
        "es_premium": vigente,
        "hasta": user.premium_until,
        "dias_restantes": (
            (user.premium_until - timezone.localdate()).days
            if vigente and user.premium_until else None
        ),
    }
