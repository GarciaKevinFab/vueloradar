"""Premium pagado con Telegram Stars.

Acá se prueba lo que cuesta plata si sale mal: acreditar dos veces el mismo
pago, pisarle a alguien los días que ya había comprado, o dejar premium a
alguien cuyo plazo venció.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.models import StarPayment, TelegramUser
from apps.users.payments import PLANES, acreditar_pago, estado_premium, plan_por_clave

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return TelegramUser.objects.create(telegram_id=123, first_name="Kevin")


# --- lo que cuesta plata ------------------------------------------------------

def test_el_mismo_pago_no_se_acredita_dos_veces(user):
    """Telegram reenvía el update si no recibe confirmación.

    Sin esta garantía, un reintento le regalaría a la persona el doble de días
    — y nadie lo reclamaría, así que nunca nos enteraríamos.
    """
    primera = acreditar_pago(user, charge_id="stpm_0001", estrellas=150, dias=30)
    segunda = acreditar_pago(user, charge_id="stpm_0001", estrellas=150, dias=30)

    assert primera.ya_estaba_acreditado is False
    assert segunda.ya_estaba_acreditado is True
    assert primera.hasta == segunda.hasta          # no sumó de nuevo
    assert StarPayment.objects.count() == 1


def test_comprar_de_nuevo_suma_dias_en_vez_de_pisarlos(user):
    """Si le quedan 10 días y compra 30, termina con 40, no con 30."""
    acreditar_pago(user, charge_id="stpm_0001", estrellas=150, dias=30)
    primera_fecha = TelegramUser.objects.get(pk=user.pk).premium_until

    segunda = acreditar_pago(user, charge_id="stpm_0002", estrellas=150, dias=30)

    assert segunda.hasta == primera_fecha + timedelta(days=30)


def test_si_ya_vencio_se_cuenta_desde_hoy(user):
    """Un premium vencido hace meses no puede arrastrar la fecha vieja."""
    user.plan = TelegramUser.PLAN_PREMIUM
    user.premium_until = timezone.localdate() - timedelta(days=200)
    user.save()

    acreditacion = acreditar_pago(user, charge_id="stpm_0003", estrellas=150, dias=30)

    assert acreditacion.hasta == timezone.localdate() + timedelta(days=30)


def test_se_guarda_el_cargo_para_poder_reembolsar(user):
    """`telegram_payment_charge_id` es lo único que permite devolver la plata,
    y Telegram no lo vuelve a entregar."""
    acreditar_pago(user, charge_id="stpm_0004", estrellas=390, dias=90)

    pago = StarPayment.objects.get(charge_id="stpm_0004")
    assert pago.stars == 390
    assert pago.days == 90
    assert pago.user_id == user.pk


# --- vigencia ------------------------------------------------------------------

def test_el_premium_vence(user):
    user.plan = TelegramUser.PLAN_PREMIUM
    user.premium_until = timezone.localdate() - timedelta(days=1)
    user.save()
    assert user.is_premium is False


def test_el_ultimo_dia_todavia_cuenta(user):
    """Vencer «hoy» significa que hoy todavía se puede usar."""
    user.plan = TelegramUser.PLAN_PREMIUM
    user.premium_until = timezone.localdate()
    user.save()
    assert user.is_premium is True


def test_sin_fecha_el_premium_no_caduca(user):
    """Para que el admin pueda regalar acceso sin inventar una fecha lejana."""
    user.plan = TelegramUser.PLAN_PREMIUM
    user.premium_until = None
    user.save()
    assert user.is_premium is True


def test_el_plan_gratis_nunca_es_premium(user):
    user.premium_until = timezone.localdate() + timedelta(days=999)
    user.save()
    assert user.plan == TelegramUser.PLAN_FREE
    assert user.is_premium is False


def test_pagar_levanta_los_limites(user):
    """La razón por la que alguien paga: dejar de chocar contra el cupo."""
    user.searches_today = 10
    user.save()
    assert user.can_search(10) is False

    acreditar_pago(user, charge_id="stpm_0005", estrellas=150, dias=30)

    user.refresh_from_db()
    assert user.can_search(10) is True
    assert user.remaining_searches(10) is None      # ilimitado


# --- los planes ----------------------------------------------------------------

def test_los_paquetes_largos_salen_mas_baratos_por_mes():
    """Si el anual no conviene, nadie lo compra y solo agrega una opción."""
    por_mes = [PLANES[c].por_mes for c in ("mes", "trimestre", "anio")]
    assert por_mes == sorted(por_mes, reverse=True)


def test_un_plan_inexistente_no_revienta():
    """El `callback_data` de un botón viejo llega igual después de un cambio."""
    assert plan_por_clave("plan-que-ya-no-existe") is None


def test_el_estado_informa_los_dias_restantes(user):
    acreditar_pago(user, charge_id="stpm_0006", estrellas=150, dias=30)
    user.refresh_from_db()

    estado = estado_premium(user)
    assert estado["es_premium"] is True
    assert estado["dias_restantes"] == 30


def test_el_estado_de_un_usuario_gratis_no_inventa_fechas(user):
    estado = estado_premium(user)
    assert estado["es_premium"] is False
    assert estado["hasta"] is None
    assert estado["dias_restantes"] is None


# --- la oferta ------------------------------------------------------------------

def test_la_oferta_no_vende_lo_que_ya_es_gratis():
    """Regresión de un texto que llegué a escribir: prometía «veredicto de
    compra» y «aviso apenas baja el precio» como exclusivos de premium, y las
    dos cosas ya funcionan sin pagar. Cobrar por algo que la persona ya tiene
    es la forma más rápida de perder la credibilidad que sostiene el producto.
    """
    from bot import formatting

    texto = formatting.premium_offer({"es_premium": False})
    assert "funcionan igual en el plan gratis" in texto
    assert "Búsquedas sin límite" in texto
