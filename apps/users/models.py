"""Usuarios del bot de Telegram y su plan de uso."""

from django.db import models
from django.utils import timezone


class TelegramUser(models.Model):
    """Alguien que le habla al bot.

    El límite del plan free se resetea de forma perezosa: no hay task nocturna,
    se compara `searches_reset_date` con hoy en la primera búsqueda del día.
    """

    PLAN_FREE = "free"
    PLAN_PREMIUM = "premium"
    PLAN_CHOICES = [
        (PLAN_FREE, "Gratis"),
        (PLAN_PREMIUM, "Premium"),
    ]

    telegram_id = models.BigIntegerField("ID de Telegram", unique=True, db_index=True)
    username = models.CharField("usuario", max_length=64, blank=True)
    first_name = models.CharField("nombre", max_length=128, blank=True)

    plan = models.CharField("plan", max_length=10, choices=PLAN_CHOICES, default=PLAN_FREE)
    #: Hasta cuándo vale el premium. Nulo = no caduca (regalo del admin).
    premium_until = models.DateField("premium hasta", null=True, blank=True)

    created_at = models.DateTimeField("registrado en", auto_now_add=True)
    last_active_at = models.DateTimeField("última actividad", default=timezone.now)

    searches_today = models.PositiveIntegerField("búsquedas de hoy", default=0)
    searches_reset_date = models.DateField("contador del día", default=timezone.localdate)

    class Meta:
        verbose_name = "usuario de Telegram"
        verbose_name_plural = "usuarios de Telegram"
        ordering = ["-last_active_at"]

    def __str__(self) -> str:
        etiqueta = self.username or self.first_name or str(self.telegram_id)
        return f"{etiqueta} ({self.get_plan_display()})"

    @property
    def is_premium(self) -> bool:
        """Premium vigente: el plan **y** que no esté vencido.

        Sigue siendo propiedad y no método: se usa sin paréntesis en
        `users/services.py`, `bot/db.py` y el motor de alertas, y un método
        devolvería siempre un objeto verdadero en esos `if`.

        Sin `premium_until` el premium no caduca, para que el admin pueda
        regalar acceso permanente sin inventar una fecha lejana.
        """
        if self.plan != self.PLAN_PREMIUM:
            return False
        if self.premium_until is None:
            return True
        return self.premium_until >= timezone.localdate()

    def reset_counter_if_needed(self, today=None) -> bool:
        """Pone el contador en cero si cambió el día. Devuelve si reseteó."""
        today = today or timezone.localdate()
        if self.searches_reset_date >= today:
            return False
        self.searches_today = 0
        self.searches_reset_date = today
        return True

    def remaining_searches(self, limit: int, today=None) -> int | None:
        """Búsquedas que le quedan hoy. `None` = ilimitadas (premium)."""
        if self.is_premium:
            return None
        self.reset_counter_if_needed(today)
        return max(limit - self.searches_today, 0)

    def can_search(self, limit: int, today=None) -> bool:
        remaining = self.remaining_searches(limit, today)
        return remaining is None or remaining > 0


class StarPayment(models.Model):
    """Un pago en Telegram Stars.

    Se guarda sobre todo por el `charge_id`: es lo único que permite
    reembolsar con `refundStarPayment`, y Telegram no lo vuelve a dar. Sin esta
    tabla, devolverle la plata a alguien sería imposible.

    También es el registro contable: cuántas estrellas entraron y por qué.
    """

    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE, related_name="star_payments",
        verbose_name="usuario",
    )
    #: `telegram_payment_charge_id`. Único: si Telegram reenvía el update, el
    #: pago no se acredita dos veces.
    charge_id = models.CharField("cargo de Telegram", max_length=128, unique=True)
    stars = models.PositiveIntegerField("estrellas")
    days = models.PositiveIntegerField("días de premium")
    created_at = models.DateTimeField("pagado en", auto_now_add=True)
    refunded_at = models.DateTimeField("reembolsado en", null=True, blank=True)

    class Meta:
        verbose_name = "pago en estrellas"
        verbose_name_plural = "pagos en estrellas"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.stars}⭐ de {self.user_id} ({self.days} días)"
