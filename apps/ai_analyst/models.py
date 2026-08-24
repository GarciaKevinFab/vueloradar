"""Contabilidad del consumo de IA.

Una fila por día y proveedor: sirve para ver a quién se le está pagando y
detectar cuándo la cadena de respaldo está trabajando más de lo esperado.
"""

from django.db import models
from django.utils import timezone


class AIUsageLog(models.Model):
    """Consumo acumulado de un proveedor en un día."""

    date = models.DateField("fecha", default=timezone.localdate, db_index=True)
    provider = models.CharField("proveedor", max_length=32)

    calls = models.PositiveIntegerField("llamadas", default=0)
    failures = models.PositiveIntegerField("fallos", default=0)
    input_tokens = models.PositiveIntegerField("tokens de entrada", default=0)
    output_tokens = models.PositiveIntegerField("tokens de salida", default=0)

    class Meta:
        verbose_name = "consumo de IA"
        verbose_name_plural = "consumo de IA"
        ordering = ["-date", "provider"]
        constraints = [
            models.UniqueConstraint(fields=["date", "provider"], name="uniq_ai_usage_date_provider"),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.provider}: {self.calls} llamadas"
