"""Datos comunes a todas las plantillas públicas."""

from django.conf import settings


def site(request):
    """Marca, enlace al bot, crédito del constructor y publicidad.

    Todo sale de settings: la plantilla no decide nada, solo pinta lo que hay.
    """
    usuario = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
    return {
        "site_name": getattr(settings, "SITE_NAME", "VueloRadar"),
        "bot_url": f"https://t.me/{usuario}" if usuario else "",
        "builder_name": getattr(settings, "BUILDER_NAME", ""),
        "builder_url": getattr(settings, "BUILDER_URL", ""),
        # Vacío = crédito en texto. Ver la nota en settings: `{% static %}` con
        # manifiesto revienta si el archivo no está en disco.
        "builder_logo": getattr(settings, "BUILDER_LOGO", ""),
        # Sin ID de editor no se renderiza ni el hueco ni el script: un
        # contenedor vacío reservando alto desplaza el contenido para nada.
        "adsense_client": getattr(settings, "ADSENSE_CLIENT", ""),
        "adsense_slot_home": getattr(settings, "ADSENSE_SLOT_HOME", ""),
        "adsense_slot_route": getattr(settings, "ADSENSE_SLOT_ROUTE", ""),
        # Verificación de Search Console. Público, no es un secreto.
        "google_site_verification": getattr(settings, "GOOGLE_SITE_VERIFICATION", ""),
    }
