"""Datos comunes a todas las plantillas públicas."""

from django.conf import settings


def site(request):
    """Marca, enlace al bot y publicidad.

    Todo sale de settings: la plantilla no decide nada, solo pinta lo que hay.

    El crédito del constructor ya NO pasa por acá: el distintivo se volvió un
    componente portable con el nombre, el enlace y el logo dentro del marcado,
    para poder pegarlo en otro sitio sin arrastrar settings. Las tres variables
    `BUILDER_*` que lo alimentaban quedaron sin usar y se eliminaron.
    """
    usuario = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
    return {
        "site_name": getattr(settings, "SITE_NAME", "VueloRadar"),
        "bot_url": f"https://t.me/{usuario}" if usuario else "",
        # Sin ID de editor no se renderiza ni el hueco ni el script: un
        # contenedor vacío reservando alto desplaza el contenido para nada.
        "adsense_client": getattr(settings, "ADSENSE_CLIENT", ""),
        "adsense_slot_home": getattr(settings, "ADSENSE_SLOT_HOME", ""),
        "adsense_slot_route": getattr(settings, "ADSENSE_SLOT_ROUTE", ""),
        # Verificación de Search Console. Público, no es un secreto.
        "google_site_verification": getattr(settings, "GOOGLE_SITE_VERIFICATION", ""),
        # Vacío mientras el buzón no exista: una dirección que rebota es peor
        # que no publicar ninguna.
        "contact_email": getattr(settings, "CONTACT_EMAIL", ""),
        # Analítica sin cookies. Sin token no se carga el script.
        "cf_analytics_token": getattr(settings, "CLOUDFLARE_ANALYTICS_TOKEN", ""),
        # Distinto de lo anterior: Cloudflare puede inyectar el beacon en el
        # borde sin que nuestro HTML lo mencione. Esto es lo que mira la
        # política de privacidad, para que declare lo que de verdad ocurre.
        "analytics_activa": bool(
            getattr(settings, "CLOUDFLARE_ANALYTICS_TOKEN", "")
            or getattr(settings, "ANALYTICS_ENABLED", False)
        ),
    }
