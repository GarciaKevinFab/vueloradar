"""Límite diario del plan gratuito y reseteo perezoso del contador."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.models import TelegramUser
from apps.users.services import check_quota, consume_search, get_or_create_user


@pytest.fixture
def user(db):
    return TelegramUser.objects.create(telegram_id=500000001, first_name="Kevin")


@pytest.mark.django_db
def test_alta_de_usuario_nuevo():
    user = get_or_create_user(123, username="kevin", first_name="Kevin")

    assert user.telegram_id == 123
    assert user.plan == TelegramUser.PLAN_FREE
    assert user.searches_today == 0
    assert TelegramUser.objects.count() == 1


@pytest.mark.django_db
def test_segundo_start_no_duplica_y_refresca_el_perfil():
    get_or_create_user(123, username="viejo", first_name="Kevin")
    user = get_or_create_user(123, username="nuevo", first_name="Kevin G")

    assert TelegramUser.objects.count() == 1
    assert user.username == "nuevo"
    assert user.first_name == "Kevin G"


@pytest.mark.django_db
def test_free_agota_el_cupo_en_la_busqueda_once(user, settings):
    settings.FREE_DAILY_SEARCHES = 10

    for i in range(10):
        assert check_quota(user).allowed is True, f"bloqueado en la búsqueda {i + 1}"
        consume_search(user)

    quota = check_quota(user)
    assert quota.allowed is False
    assert quota.remaining == 0
    assert quota.limit == 10


@pytest.mark.django_db
def test_el_contador_baja_de_a_uno(user, settings):
    settings.FREE_DAILY_SEARCHES = 10

    assert check_quota(user).remaining == 10
    consume_search(user)
    assert check_quota(user).remaining == 9
    consume_search(user)
    assert check_quota(user).remaining == 8


@pytest.mark.django_db
def test_premium_es_ilimitado(user, settings):
    settings.FREE_DAILY_SEARCHES = 10
    user.plan = TelegramUser.PLAN_PREMIUM
    user.save()

    for _ in range(25):
        assert check_quota(user).allowed is True
        consume_search(user)

    quota = check_quota(user)
    assert quota.allowed is True
    assert quota.remaining is None
    assert quota.is_premium is True
    assert user.searches_today == 0, "premium no consume el contador"


@pytest.mark.django_db
def test_el_contador_se_resetea_solo_al_cambiar_el_dia(user, settings):
    settings.FREE_DAILY_SEARCHES = 10

    for _ in range(10):
        consume_search(user)
    assert check_quota(user).allowed is False

    # Simula que la última búsqueda fue ayer.
    ayer = timezone.localdate() - timedelta(days=1)
    TelegramUser.objects.filter(pk=user.pk).update(searches_reset_date=ayer)
    user.refresh_from_db()

    quota = check_quota(user)
    assert quota.allowed is True
    assert quota.remaining == 10


@pytest.mark.django_db
def test_el_reseteo_no_se_dispara_dos_veces_el_mismo_dia(user):
    assert user.reset_counter_if_needed() is False

    user.searches_reset_date = timezone.localdate() - timedelta(days=1)
    user.searches_today = 7
    assert user.reset_counter_if_needed() is True
    assert user.searches_today == 0
    assert user.reset_counter_if_needed() is False


@pytest.mark.django_db
def test_can_search_respeta_el_limite(user, settings):
    settings.FREE_DAILY_SEARCHES = 2

    assert user.can_search(2) is True
    user.searches_today = 2
    assert user.can_search(2) is False
