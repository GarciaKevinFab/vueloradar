"""Purga del caché de Cloudflare cuando entran datos nuevos.

Las páginas públicas declaran `s-maxage=1800`, así que entre barridos las
sirve el borde y el VPS ni se entera. El precio de eso es que, al terminar un
barrido, el borde queda con datos viejos hasta media hora. Por eso purgamos
explícitamente al final de cada recálculo de estadísticas.

Sin `CLOUDFLARE_API_TOKEN` la función no hace nada y lo dice en el log: en
desarrollo no hay borde que purgar y esto no debe romper el barrido.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

API = "https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache"
TIMEOUT = 15


def purge_everything() -> bool:
    """Invalida todo el caché de la zona. Devuelve si la purga se aplicó.

    Purgamos la zona entera y no URL por URL porque son ~40 páginas: el costo
    es el mismo y evita olvidarse de la portada, el sitemap o una ruta nueva.
    Nunca lanza: un fallo de Cloudflare no puede tumbar el barrido.
    """
    token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
    zone = getattr(settings, "CLOUDFLARE_ZONE_ID", "")
    if not token or not zone:
        logger.debug("cloudflare: sin token o zona configurada, no se purga")
        return False

    req = urllib.request.Request(
        API.format(zone=zone),
        data=json.dumps({"purge_everything": True}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("cloudflare: fallo al purgar el caché: %s", exc)
        return False

    if not payload.get("success"):
        logger.warning("cloudflare: purga rechazada: %s", payload.get("errors"))
        return False

    logger.info("cloudflare: caché de la zona purgado")
    return True
