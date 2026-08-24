"""Operaciones sobre usuarios del bot, todas síncronas.

El bot es async; envuelve estas funciones con `sync_to_async` (ver bot/db.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import TelegramUser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaCheck:
    """Resultado de validar el cupo diario de un usuario."""

    allowed: bool
    remaining: int | None      # None = ilimitado
    limit: int
    is_premium: bool


@transaction.atomic
def get_or_create_user(telegram_id: int, *, username: str = "", first_name: str = "") -> TelegramUser:
    """Registra al usuario si es nuevo y refresca sus datos de perfil."""
    user, created = TelegramUser.objects.get_or_create(
        telegram_id=telegram_id,
        defaults={"username": username or "", "first_name": first_name or ""},
    )

    campos = ["last_active_at"]
    user.last_active_at = timezone.now()
    if username and user.username != username:
        user.username = username
        campos.append("username")
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        campos.append("first_name")
    user.save(update_fields=campos)

    if created:
        logger.info("users: alta de %s", telegram_id)
    return user


def check_quota(user: TelegramUser) -> QuotaCheck:
    """Mira si al usuario le queda cupo, sin consumirlo."""
    limit = settings.FREE_DAILY_SEARCHES
    if user.is_premium:
        return QuotaCheck(allowed=True, remaining=None, limit=limit, is_premium=True)

    if user.reset_counter_if_needed():
        user.save(update_fields=["searches_today", "searches_reset_date"])

    remaining = max(limit - user.searches_today, 0)
    return QuotaCheck(allowed=remaining > 0, remaining=remaining, limit=limit, is_premium=False)


@transaction.atomic
def consume_search(user: TelegramUser) -> QuotaCheck:
    """Descuenta una búsqueda del cupo diario y devuelve cómo quedó.

    Se llama **después** de validar con `check_quota`. Premium no consume.
    """
    limit = settings.FREE_DAILY_SEARCHES
    if user.is_premium:
        return QuotaCheck(allowed=True, remaining=None, limit=limit, is_premium=True)

    locked = TelegramUser.objects.select_for_update().get(pk=user.pk)
    locked.reset_counter_if_needed()
    locked.searches_today += 1
    locked.save(update_fields=["searches_today", "searches_reset_date"])

    user.searches_today = locked.searches_today
    user.searches_reset_date = locked.searches_reset_date

    remaining = max(limit - locked.searches_today, 0)
    return QuotaCheck(allowed=remaining > 0, remaining=remaining, limit=limit, is_premium=False)
