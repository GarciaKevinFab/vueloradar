"""Endpoint de salud para el healthcheck de Docker.

Un 200 significa que Django arrancó **y** que Supabase responde. Sin la
consulta, el contenedor se vería sano con la base caída.
"""

from __future__ import annotations

import logging

from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.error("healthz: la base no responde: %s", exc)
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)

    return JsonResponse({"status": "ok", "database": "ok"})
