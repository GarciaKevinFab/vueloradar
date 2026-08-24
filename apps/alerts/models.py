"""Alertas de precio y su historial de disparos."""

from django.db import models
from django.utils import timezone


class Alert(models.Model):
    """Lo que un usuario quiere que le avisen."""

    TYPE_PRICE_BELOW = "price_below"
    TYPE_DEAL_DETECTED = "deal_detected"
    TYPE_CHOICES = [
        (TYPE_PRICE_BELOW, "Precio por debajo de"),
        (TYPE_DEAL_DETECTED, "Oferta detectada"),
    ]

    user = models.ForeignKey(
        "users.TelegramUser", on_delete=models.CASCADE, related_name="alerts",
        verbose_name="usuario",
    )
    route = models.ForeignKey(
        "flights.Route", on_delete=models.CASCADE, related_name="alerts", verbose_name="ruta"
    )

    #: null = cualquier fecha de vuelo de esa ruta.
    flight_date = models.DateField("fecha del vuelo", null=True, blank=True)
    #: Solo para price_below.
    target_price_pen = models.DecimalField(
        "precio objetivo S/", max_digits=10, decimal_places=2, null=True, blank=True
    )

    alert_type = models.CharField("tipo", max_length=20, choices=TYPE_CHOICES)
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField("creada en", auto_now_add=True)

    class Meta:
        verbose_name = "alerta"
        verbose_name_plural = "alertas"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["route", "is_active"], name="idx_alert_route_active"),
            models.Index(fields=["user", "is_active"], name="idx_alert_user_active"),
        ]

    def __str__(self) -> str:
        if self.alert_type == self.TYPE_PRICE_BELOW:
            return f"{self.route} bajo S/ {self.target_price_pen}"
        return f"{self.route} ofertas"

    @property
    def describe(self) -> str:
        """Cómo se le muestra al usuario en /misalertas."""
        cuando = self.flight_date.strftime("%d/%m/%Y") if self.flight_date else "cualquier fecha"
        if self.alert_type == self.TYPE_PRICE_BELOW:
            return f"{self.route.origin_id}→{self.route.destination_id} bajo S/ {self.target_price_pen:,.0f} · {cuando}"
        return f"{self.route.origin_id}→{self.route.destination_id} ofertas · {cuando}"

    def matches_date(self, flight_date) -> bool:
        """Una alerta sin fecha vale para cualquier fecha de vuelo."""
        return self.flight_date is None or self.flight_date == flight_date


class AlertTrigger(models.Model):
    """Cada vez que una alerta se disparó.

    Guarda el precio para poder aplicar el anti-spam: no re-avisar si el
    precio no bajó lo suficiente respecto del último aviso.
    """

    alert = models.ForeignKey(
        Alert, on_delete=models.CASCADE, related_name="triggers", verbose_name="alerta"
    )
    snapshot = models.ForeignKey(
        "flights.PriceSnapshot", on_delete=models.CASCADE, related_name="triggers",
        verbose_name="snapshot",
    )

    price_pen = models.DecimalField("precio S/", max_digits=10, decimal_places=2)
    message_sent = models.BooleanField("mensaje enviado", default=False)
    triggered_at = models.DateTimeField("disparada en", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "disparo de alerta"
        verbose_name_plural = "disparos de alerta"
        ordering = ["-triggered_at"]
        indexes = [
            models.Index(fields=["alert", "triggered_at"], name="idx_trigger_alert_when"),
        ]

    def __str__(self) -> str:
        return f"{self.alert} a S/ {self.price_pen} ({self.triggered_at:%d/%m %H:%M})"
