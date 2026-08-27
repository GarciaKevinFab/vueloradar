"""Avisos por correo: doble opt-in, baja en un clic y entrega.

Telegram tiene ~6% de penetración en Perú. El correo existe para no perder al
resto del embudo, pero un formulario público que manda correo necesita
confirmación: cualquiera puede escribir la dirección de otra persona.
"""

from datetime import date

import pytest
from django.core import mail
from django.urls import reverse

from apps.alerts.models import Alert


@pytest.fixture(autouse=True)
def _cache_limpio():
    """El contador por IP vive en cache y se acumularia entre tests."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def route(peru_airports):
    from apps.flights.models import Route

    return Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True)


def _alta(client, route, correo="viajero@ejemplo.pe", fecha=None):
    datos = {"email": correo, "ruta": f"{route.origin_id}-{route.destination_id}"}
    if fecha:
        datos["fecha"] = fecha
    return client.post(reverse("web:nuevo_aviso"), datos)


# --- alta y doble opt-in -----------------------------------------------------

def test_el_alta_crea_la_alerta_sin_confirmar(client, route):
    resp = _alta(client, route)
    assert resp.status_code == 200

    alerta = Alert.objects.get(email="viajero@ejemplo.pe")
    assert alerta.email_confirmed_at is None
    assert alerta.puede_notificar is False
    assert alerta.token


def test_el_alta_manda_el_correo_de_confirmacion(client, route):
    _alta(client, route)
    assert len(mail.outbox) == 1
    assert "Confirmá" in mail.outbox[0].subject
    assert mail.outbox[0].to == ["viajero@ejemplo.pe"]
    # El enlace de confirmacion tiene que estar en el cuerpo.
    assert "/aviso/confirmar/" in mail.outbox[0].body


def test_sin_confirmar_no_llega_ningun_aviso(client, route):
    """Es la garantía que hace honesto el formulario público."""
    _alta(client, route)
    alerta = Alert.objects.get(email="viajero@ejemplo.pe")
    assert alerta.puede_notificar is False


def test_confirmar_activa_el_aviso(client, route):
    _alta(client, route)
    alerta = Alert.objects.get(email="viajero@ejemplo.pe")

    resp = client.get(reverse("web:confirmar_aviso", args=[alerta.token]))
    assert resp.status_code == 200

    alerta.refresh_from_db()
    assert alerta.email_confirmed_at is not None
    assert alerta.puede_notificar is True


def test_confirmar_dos_veces_no_rompe(client, route):
    _alta(client, route)
    token = Alert.objects.get(email="viajero@ejemplo.pe").token
    assert client.get(reverse("web:confirmar_aviso", args=[token])).status_code == 200
    assert client.get(reverse("web:confirmar_aviso", args=[token])).status_code == 200


def test_un_token_inventado_da_404(client, route):
    assert client.get(reverse("web:confirmar_aviso", args=["no-existe"])).status_code == 404


# --- baja --------------------------------------------------------------------

def test_la_baja_desactiva_en_un_clic(client, route):
    _alta(client, route)
    alerta = Alert.objects.get(email="viajero@ejemplo.pe")
    client.get(reverse("web:confirmar_aviso", args=[alerta.token]))

    resp = client.get(reverse("web:baja_aviso", args=[alerta.token]))
    assert resp.status_code == 200

    alerta.refresh_from_db()
    assert alerta.is_active is False


# --- validacion y abuso -------------------------------------------------------

def test_un_correo_invalido_no_crea_nada(client, route):
    resp = _alta(client, route, correo="esto no es un correo")
    assert resp.status_code == 200
    assert "no parece válido" in resp.content.decode()
    assert Alert.objects.count() == 0
    assert len(mail.outbox) == 0


def test_pedir_el_mismo_aviso_dos_veces_no_duplica(client, route):
    _alta(client, route)
    _alta(client, route)
    assert Alert.objects.filter(email="viajero@ejemplo.pe").count() == 1


def test_se_limita_por_ip(client, route, settings):
    """Un formulario público que manda correo es un vector de spam."""
    settings.EMAIL_ALERTS_PER_IP_PER_HOUR = 2
    for i in range(4):
        _alta(client, route, correo=f"v{i}@ejemplo.pe")
    # Pasado el tope deja de crear alertas nuevas.
    assert Alert.objects.count() <= 2


def test_una_ruta_desconocida_da_404(client, peru_airports):
    assert client.get(reverse("web:nuevo_aviso"), {"ruta": "ZZZ-YYY"}).status_code == 404


# --- por fecha concreta -------------------------------------------------------

def test_se_puede_pedir_para_una_fecha(client, route):
    _alta(client, route, fecha="2026-10-14")
    alerta = Alert.objects.get(email="viajero@ejemplo.pe")
    assert alerta.flight_date == date(2026, 10, 14)
    assert alerta.matches_date(date(2026, 10, 14)) is True
    assert alerta.matches_date(date(2026, 10, 15)) is False
