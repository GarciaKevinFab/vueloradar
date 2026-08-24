"""Resiliencia de la fuente: lock, contador de fallos y pausa de 30 minutos."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.scraping import ratelimit

SOURCE = "google_flights"
SEARCH_DATE = date(2026, 9, 15)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


# --------------------------------------------------------- contador de fallos
def test_los_fallos_se_acumulan():
    assert [ratelimit.record_failure(SOURCE) for _ in range(3)] == [1, 2, 3]
    assert ratelimit.failure_count(SOURCE) == 3


def test_un_exito_borra_el_historial():
    ratelimit.record_failure(SOURCE)
    ratelimit.record_failure(SOURCE)
    ratelimit.record_success(SOURCE)

    assert ratelimit.failure_count(SOURCE) == 0
    assert ratelimit.should_pause(SOURCE) is False


def test_should_pause_recien_al_tercer_fallo():
    ratelimit.record_failure(SOURCE)
    assert ratelimit.should_pause(SOURCE) is False
    ratelimit.record_failure(SOURCE)
    assert ratelimit.should_pause(SOURCE) is False
    ratelimit.record_failure(SOURCE)
    assert ratelimit.should_pause(SOURCE) is True


def test_las_fuentes_se_cuentan_por_separado():
    ratelimit.record_failure("google_flights")
    ratelimit.record_failure("google_flights")
    ratelimit.record_failure("sky")

    assert ratelimit.failure_count("google_flights") == 2
    assert ratelimit.failure_count("sky") == 1


# ------------------------------------------------------------------ pausa
def test_pause_y_resume():
    assert ratelimit.is_paused(SOURCE) is False
    assert ratelimit.pause(SOURCE, 1800) == 1800
    assert ratelimit.is_paused(SOURCE) is True

    ratelimit.resume(SOURCE)
    assert ratelimit.is_paused(SOURCE) is False
    assert ratelimit.failure_count(SOURCE) == 0


def test_pause_usa_los_30_minutos_de_settings(settings):
    settings.SOURCE_PAUSE_SECONDS = 1800
    assert ratelimit.pause(SOURCE) == 1800


# ------------------------------------------------------------------- lock
def test_el_lock_es_exclusivo():
    with ratelimit.source_lock(SOURCE):
        with pytest.raises(ratelimit.SourceBusy):
            with ratelimit.source_lock(SOURCE):
                pass


def test_el_lock_se_libera_al_salir():
    with ratelimit.source_lock(SOURCE):
        pass
    with ratelimit.source_lock(SOURCE):
        pass  # si siguiera tomado, esto reventaría


def test_el_lock_se_libera_aunque_el_bloque_falle():
    with pytest.raises(RuntimeError):
        with ratelimit.source_lock(SOURCE):
            raise RuntimeError("scraping explotó")

    with ratelimit.source_lock(SOURCE):
        pass


def test_fuentes_distintas_no_se_bloquean_entre_si():
    with ratelimit.source_lock("google_flights"):
        with ratelimit.source_lock("sky"):
            pass
