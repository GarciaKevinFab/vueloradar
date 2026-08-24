"""Granularidad del barrido: qué fechas de vuelo se consultan en cada corrida.

Los precios se mueven mucho cerca de la salida y poco a 2 meses vista, así que
el horizonte se muestrea denso al principio y ralo después (CLAUDE.md secc. 7):
todos los días los próximos 14, y cada 3 días hasta el día 60.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings


def build_scan_dates(
    start: date,
    *,
    daily_days: int | None = None,
    max_days: int | None = None,
    sparse_step: int | None = None,
) -> list[date]:
    """Devuelve las fechas de vuelo a consultar, ordenadas y sin repetidos.

    Args:
        start: día de referencia (normalmente hoy en hora Perú). El barrido
            arranca al día siguiente: hoy ya no se puede comprar con sentido.
        daily_days: cuántos días consultar uno por uno.
        max_days: hasta qué día del horizonte llegar.
        sparse_step: cada cuántos días muestrear pasada la ventana densa.
    """
    daily_days = settings.SCAN_DAILY_HORIZON_DAYS if daily_days is None else daily_days
    max_days = settings.SCAN_MAX_HORIZON_DAYS if max_days is None else max_days
    sparse_step = settings.SCAN_SPARSE_STEP_DAYS if sparse_step is None else sparse_step

    if sparse_step < 1:
        raise ValueError("sparse_step debe ser al menos 1")

    offsets = set(range(1, min(daily_days, max_days) + 1))
    offsets |= set(range(daily_days + 1, max_days + 1, sparse_step))

    return [start + timedelta(days=offset) for offset in sorted(offsets)]
