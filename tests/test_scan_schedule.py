"""Granularidad del barrido: 14 días diarios + salteados hasta el día 60."""

from datetime import date, timedelta

import pytest

from apps.scraping.schedule import build_scan_dates

TODAY = date(2026, 9, 1)


def offsets(dates):
    return [(d - TODAY).days for d in dates]


def test_arranca_manana_nunca_hoy():
    result = build_scan_dates(TODAY)
    assert min(offsets(result)) == 1
    assert TODAY not in result


def test_los_primeros_catorce_dias_son_consecutivos():
    result = offsets(build_scan_dates(TODAY))
    assert result[:14] == list(range(1, 15))


def test_despues_del_dia_catorce_saltea_de_a_tres():
    result = offsets(build_scan_dates(TODAY))
    sparse = [o for o in result if o > 14]
    assert sparse == list(range(15, 61, 3))
    assert all(b - a == 3 for a, b in zip(sparse, sparse[1:]))


def test_no_pasa_del_horizonte():
    result = offsets(build_scan_dates(TODAY))
    assert max(result) <= 60


def test_sin_fechas_repetidas_y_ordenadas():
    result = build_scan_dates(TODAY)
    assert len(result) == len(set(result))
    assert result == sorted(result)


def test_volumen_por_ruta():
    """14 diarios + 16 salteados = 30 consultas por ruta y barrido."""
    assert len(build_scan_dates(TODAY)) == 30


def test_parametros_explicitos():
    result = offsets(build_scan_dates(TODAY, daily_days=3, max_days=10, sparse_step=2))
    assert result == [1, 2, 3, 4, 6, 8, 10]


def test_horizonte_mas_corto_que_la_ventana_diaria():
    result = offsets(build_scan_dates(TODAY, daily_days=14, max_days=5, sparse_step=3))
    assert result == [1, 2, 3, 4, 5]


def test_step_invalido():
    with pytest.raises(ValueError):
        build_scan_dates(TODAY, sparse_step=0)


def test_respeta_el_dia_de_referencia_que_se_le_pasa():
    otro = date(2027, 1, 15)
    assert build_scan_dates(otro)[0] == otro + timedelta(days=1)
