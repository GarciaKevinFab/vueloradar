"""Correos de alerta: confirmación y aviso de precio.

Todo correo lleva enlace de baja, y ninguno se manda antes de que la persona
confirme. Cualquiera puede escribir el correo de otro en un formulario público:
sin doble opt-in esto sería una máquina de spam con buenas intenciones.

Los mensajes van en texto plano y HTML mínimo. Un correo transaccional que
parece un folleto entra en promociones y no lo lee nadie.
"""

from __future__ import annotations

import logging
import re
import secrets

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse

logger = logging.getLogger(__name__)

#: El mensaje de Telegram viene con etiquetas HTML simples; para el texto plano
#: se limpian sin traer una dependencia de parseo.
_TAGS = re.compile(r"<[^>]+>")
#: Los enlaces se expanden antes de limpiar: si no, el texto plano se queda sin URL.
_ENLACE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
#: Cierres de bloque que en texto plano tienen que ser un salto de linea.
_CORTES = re.compile(r'</p>|<br\s*/?>|<hr\s*/?>', re.I)


def nuevo_token() -> str:
    """Token opaco para confirmar y para dar de baja sin contraseña."""
    return secrets.token_urlsafe(32)


def _remitente() -> str:
    return getattr(settings, "DEFAULT_FROM_EMAIL", "") or "avisos@vueloradar.com"


def _url(base_url: str, nombre: str, token: str) -> str:
    return f"{base_url.rstrip('/')}{reverse(nombre, args=[token])}"


def _a_texto(html: str) -> str:
    """Versión en texto plano del mismo mensaje.

    Los enlaces se expanden a «texto: URL» antes de limpiar las etiquetas.
    Borrarlos sin más dejaba un correo donde decía «Confirmar el aviso» y no
    había ninguna dirección: en un cliente de texto plano era imposible
    confirmar nada.
    """
    con_enlaces = _ENLACE.sub(r"\2: \1", html)
    # Sin esto los parrafos quedan pegados en una sola linea ilegible.
    con_saltos = _CORTES.sub("\n\n", con_enlaces)
    limpio = _TAGS.sub("", con_saltos)
    return re.sub(r"\n{3,}", "\n\n", limpio).strip()


def _enviar(destinatario: str, asunto: str, html: str) -> bool:
    """Manda el correo. Nunca lanza: el caller decide si reintenta."""
    try:
        mensaje = EmailMultiAlternatives(
            subject=asunto,
            body=_a_texto(html),
            from_email=_remitente(),
            to=[destinatario],
        )
        mensaje.attach_alternative(html, "text/html")
        mensaje.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - un fallo de SMTP no puede tumbar el barrido
        logger.error("mailer: no se pudo enviar a %s: %s", destinatario, exc)
        return False

    logger.info("mailer: enviado «%s» a %s", asunto, destinatario)
    return True


def send_confirmation_email(alerta, base_url: str) -> bool:
    """Pide confirmar el correo. Hasta entonces la alerta no notifica."""
    ruta = f"{alerta.route.origin.city} → {alerta.route.destination.city}"
    cuando = (
        alerta.flight_date.strftime("%d/%m/%Y") if alerta.flight_date else "cualquier fecha"
    )
    confirmar = _url(base_url, "web:confirmar_aviso", alerta.token)

    html = (
        f"<p>Alguien —esperamos que vos— pidió que avisemos cuando baje el precio de "
        f"<strong>{ruta}</strong> ({cuando}).</p>"
        f'<p><a href="{confirmar}">Confirmar el aviso</a></p>'
        f"<p>Si no fuiste vos, ignorá este correo: sin confirmar no te vamos a escribir "
        f"nunca más.</p>"
        f"<p>— VueloRadar. No vendemos pasajes ni cobramos comisión.</p>"
    )
    return _enviar(alerta.email, f"Confirmá el aviso de {ruta}", html)


def send_alert_email(alerta, texto_html: str) -> bool:
    """Aviso de precio. Mismo contenido que el mensaje de Telegram."""
    ruta = f"{alerta.route.origin.city} → {alerta.route.destination.city}"
    base = getattr(settings, "SITE_BASE_URL", "https://vueloradar.com")
    baja = _url(base, "web:baja_aviso", alerta.token)

    html = (
        f"{texto_html}"
        f"<hr>"
        f'<p style="font-size:12px;color:#666">'
        f"Recibís esto porque pediste que te avisáramos de {ruta}. "
        f'<a href="{baja}">Darme de baja</a>.</p>'
    )
    return _enviar(alerta.email, f"Bajó el precio: {ruta}", html)
