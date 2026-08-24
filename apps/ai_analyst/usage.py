"""Registro del consumo de IA, tolerante a fallos.

Contabilizar nunca debe romper una llamada al modelo: si la base falla, se
loguea y se sigue.
"""

from __future__ import annotations

import logging

from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)


def record(provider: str, *, input_tokens: int = 0, output_tokens: int = 0, failed: bool = False) -> None:
    """Suma una llamada al contador diario del proveedor."""
    from .models import AIUsageLog

    try:
        fila, _ = AIUsageLog.objects.get_or_create(
            date=timezone.localdate(), provider=provider
        )
        AIUsageLog.objects.filter(pk=fila.pk).update(
            calls=F("calls") + 1,
            failures=F("failures") + (1 if failed else 0),
            input_tokens=F("input_tokens") + input_tokens,
            output_tokens=F("output_tokens") + output_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - contabilidad nunca bloquea
        logger.warning("ai_usage: no se pudo registrar consumo de %s: %s", provider, exc)
