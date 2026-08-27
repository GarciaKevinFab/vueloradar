"""Datos comunes a todas las plantillas públicas."""

from django.conf import settings


def site(request):
    """Marca y enlace al bot, configurables por entorno."""
    usuario = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
    return {
        "site_name": getattr(settings, "SITE_NAME", "VueloRadar"),
        "bot_url": f"https://t.me/{usuario}" if usuario else "",
    }
