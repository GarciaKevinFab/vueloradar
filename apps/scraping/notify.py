"""Avisos al admin por Telegram.

El bot completo llega en Fase 3; acá alcanza con un POST a la API de Telegram.
Si no hay token o chat configurados, la función no hace nada y lo loggea: una
alerta que no se puede mandar nunca debe tumbar un barrido.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10


def send_admin_alert(text: str) -> bool:
    """Manda un mensaje al admin. Devuelve si se pudo enviar."""
    token = getattr(settings, "TELEGRAM_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning("notify: alerta no enviada (falta TELEGRAM_TOKEN o ADMIN_CHAT_ID): %s", text)
        return False

    payload = json.dumps(
        {"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            ok = json.loads(response.read().decode("utf-8")).get("ok", False)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.error("notify: fallo al enviar alerta al admin: %s", exc)
        return False

    if not ok:
        logger.error("notify: Telegram rechazó la alerta")
    return bool(ok)
