"""Correos de alerta: confirmación y aviso de precio.

Todo correo lleva enlace de baja, y ninguno se manda antes de que la persona
confirme. Cualquiera puede escribir el correo de otro en un formulario público:
sin doble opt-in esto sería una máquina de spam con buenas intenciones.

**El contenido va aparte del envoltorio.** Cada función arma un HTML simple
(párrafos y enlaces) y `_envolver` le pone la caja: tablas, anchos y colores.
Esa separación no es estética — el texto plano se deriva del contenido *antes*
de envolverlo. Si `_a_texto` viera las tablas del envoltorio, el correo en
texto plano saldría con las palabras pegadas y sin la URL de confirmación, que
es justo lo único que ese correo tiene que lograr.

El envoltorio es deliberadamente sobrio: un correo transaccional que parece un
folleto entra en promociones y no lo lee nadie. Ancho fijo de 600 px, una
tipografía de sistema y un solo color de acento.
"""

from __future__ import annotations

import logging
import re
import secrets
from html import unescape
from string import Template

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

# --- paleta -----------------------------------------------------------------
# Los mismos valores del tema claro del sitio (`base.html`). Van inline porque
# la mayoría de clientes de correo descartan las hojas de estilo.
_TINTA = "#14161a"
_TINTA_2 = "#4a4f57"
_TINTA_3 = "#8b9099"
_LINEA = "#e3e0da"
_PAPEL = "#f2f0ec"
_TARJETA = "#ffffff"
_ACENTO = "#0054d4"

_SANS = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif"
)
_SERIF = "Georgia,'Times New Roman',serif"

#: El envoltorio. `string.Template` y no f-string ni `.format()`: el bloque de
#: estilos está lleno de llaves, que ambos formatos interpretarían.
_ENVOLTORIO = Template("""\
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<style>
  /* Solo lo honran algunos clientes (Apple Mail, Outlook.com). El resto se
     queda en el tema claro, que va inline y siempre se ve bien: por eso el
     modo oscuro es una mejora y nunca un requisito. */
  @media (prefers-color-scheme: dark) {
    .papel { background: #0a0c10 !important; }
    .tarjeta { background: #14171d !important; border-color: #232830 !important; }
    .tinta { color: #f0f1f3 !important; }
    .tinta-2 { color: #a4abb6 !important; }
    .tinta-3 { color: #6b727e !important; }
  }
</style>
</head>
<body class="papel" style="margin:0;padding:0;background:$papel;">
<!-- La linea que el cliente muestra al lado del asunto. Sin esto, Gmail
     arrastra el primer texto del cuerpo y la vista previa queda a medias. -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">$preheader</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       class="papel" style="background:$papel;">
  <tr><td align="center" style="padding:32px 16px;">

    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
           class="tarjeta" style="width:100%;max-width:600px;background:$tarjeta;
           border:1px solid $linea;border-radius:14px;">
      <!-- Filo de color arriba: presencia de marca sin sumar una imagen. Un
           `background` sobre una celda de 4 px lo respetan todos los clientes,
           incluido el Outlook de escritorio. -->
      <tr><td style="height:4px;line-height:4px;font-size:0;background:$acento;">&nbsp;</td></tr>
      <tr><td style="padding:26px 32px 0;">
        <!-- La marca va en tabla y no en flex: los clientes de correo no
             soportan flexbox. `valign` alinea el logotipo con el texto. -->
        <table role="presentation" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td valign="middle" style="padding-right:10px;">
              <!-- El `alt` no es decorativo: Gmail y Outlook bloquean las
                   imagenes por defecto y sin el la cabecera queda vacia. -->
              <img src="$logo" width="34" height="34" alt="$marca"
                   style="display:block;width:34px;height:34px;border:0;border-radius:8px;">
            </td>
            <td valign="middle">
              <span class="tinta" style="font-family:$serif;font-size:22px;color:$tinta;
                    letter-spacing:-0.02em;">$marca</span>
            </td>
          </tr>
        </table>
      </td></tr>
      <tr><td class="tinta" style="padding:20px 32px 30px;font-family:$sans;
              font-size:16px;line-height:1.55;color:$tinta;">$contenido</td></tr>
    </table>

    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
           style="width:100%;max-width:600px;">
      <tr><td class="tinta-3" style="padding:18px 32px 0;font-family:$sans;
              font-size:12px;line-height:1.55;color:$tinta_3;">$pie</td></tr>
    </table>

  </td></tr>
</table>
</body>
</html>""")


def nuevo_token() -> str:
    """Token opaco para confirmar y para dar de baja sin contraseña."""
    return secrets.token_urlsafe(32)


def _remitente() -> str:
    return getattr(settings, "DEFAULT_FROM_EMAIL", "") or "avisos@vueloradar.com"


def _marca() -> str:
    return getattr(settings, "SITE_NAME", "") or "VueloRadar"


def _url(base_url: str, nombre: str, token: str) -> str:
    return f"{base_url.rstrip('/')}{reverse(nombre, args=[token])}"


def _boton(url: str, texto: str) -> str:
    """Botón de acción.

    Es un `<a>` con padding, no una tabla: así `_a_texto` lo sigue expandiendo a
    «texto: URL» sin tratamiento especial. Outlook de escritorio ignora el
    padding sobre inline-block y lo pinta como un enlace azul común — se lee
    igual, que es lo único que importa acá.
    """
    return (
        f'<p style="margin:24px 0;">'
        f'<a href="{url}" style="display:inline-block;background:{_ACENTO};'
        f'color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;'
        f'padding:13px 24px;border-radius:10px;">{texto}</a></p>'
    )


def _parrafo(texto: str, tenue: bool = False) -> str:
    color = _TINTA_2 if tenue else _TINTA
    clase = "tinta-2" if tenue else "tinta"
    return f'<p class="{clase}" style="margin:0 0 14px;color:{color};">{texto}</p>'


def _logo_url() -> str:
    """URL absoluta del logotipo.

    Tiene que ser absoluta y pública: un cliente de correo no tiene una página
    base contra la cual resolver una ruta relativa. Se resuelve por el
    manifiesto de estáticos para que el nombre con hash siga al archivo y no
    quede apuntando a una versión vieja.
    """
    from django.templatetags.static import static

    ruta = static("web/icon-192.png")
    if ruta.startswith("http"):
        return ruta
    base = (getattr(settings, "SITE_BASE_URL", "") or "https://vueloradar.com").rstrip("/")
    return f"{base}{ruta}"


def _envolver(contenido: str, preheader: str, pie: str) -> str:
    return _ENVOLTORIO.substitute(
        papel=_PAPEL, tarjeta=_TARJETA, linea=_LINEA, acento=_ACENTO,
        tinta=_TINTA, tinta_3=_TINTA_3, sans=_SANS, serif=_SERIF,
        marca=_marca(), logo=_logo_url(),
        preheader=preheader, contenido=contenido, pie=pie,
    )


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
    # Las entidades se deshacen al final, y no antes: hacerlo antes podria
    # convertir un `&lt;b&gt;` del texto en una etiqueta que `_TAGS` borraria.
    # El mensaje de Telegram trae `&amp;` escapado y sin esto llegaba crudo.
    texto = unescape(limpio)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _enviar(destinatario: str, asunto: str, contenido: str,
            preheader: str, pie: str) -> bool:
    """Manda el correo. Nunca lanza: el caller decide si reintenta.

    El texto plano sale del `contenido` crudo, no del HTML envuelto: las tablas
    del envoltorio dejarían las palabras pegadas y sin URLs.
    """
    try:
        mensaje = EmailMultiAlternatives(
            subject=asunto,
            body=f"{_a_texto(contenido)}\n\n—\n{_a_texto(pie)}",
            from_email=_remitente(),
            to=[destinatario],
        )
        mensaje.attach_alternative(_envolver(contenido, preheader, pie), "text/html")
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

    contenido = (
        _parrafo(
            f"Alguien —esperamos que vos— pidió que avisemos cuando baje el precio de "
            f"<strong>{ruta}</strong> ({cuando})."
        )
        + _boton(confirmar, "Confirmar el aviso")
        + _parrafo(
            "Si no fuiste vos, ignora este correo: sin confirmar no te vamos a "
            "escribir nunca más.",
            tenue=True,
        )
    )
    pie = (
        f"{_marca()} · No vendemos pasajes ni cobramos comisión, así que podemos "
        f"decirte que esperes."
    )
    return _enviar(
        alerta.email,
        f"Confirma el aviso de {ruta}",
        contenido,
        preheader=f"Un clic y quedás avisado de {ruta}.",
        pie=pie,
    )


def send_alert_email(alerta, texto_html: str) -> bool:
    """Aviso de precio. Mismo contenido que el mensaje de Telegram."""
    ruta = f"{alerta.route.origin.city} → {alerta.route.destination.city}"
    base = getattr(settings, "SITE_BASE_URL", "https://vueloradar.com")
    baja = _url(base, "web:baja_aviso", alerta.token)
    ficha = (
        f"{base.rstrip('/')}"
        f"/vuelos/{alerta.route.origin_id}-{alerta.route.destination_id}/"
    )

    contenido = texto_html + _boton(ficha, "Ver el histórico de la ruta")
    pie = (
        f'Recibís esto porque pediste que te avisáramos de {ruta}. '
        f'<a href="{baja}" style="color:{_TINTA_3};">Darme de baja</a>.'
    )
    return _enviar(
        alerta.email,
        f"Bajó el precio: {ruta}",
        contenido,
        preheader=f"Se movió el precio de {ruta}.",
        pie=pie,
    )
