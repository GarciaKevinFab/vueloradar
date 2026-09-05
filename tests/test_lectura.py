"""`lectura`: lo que se puede decir de una ruta y de ninguna otra.

Lógica pura sobre objetos en memoria: sin base ni red. Lo que se fija acá es
que el módulo ELIGE qué contar según el perfil, que es lo que separa una ficha
de otra. Que rellene bien los números importa menos que que dos rutas con
perfiles distintos produzcan observaciones distintas.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from apps.web import lectura
from apps.web.lectura import (
    ASIMETRIA_PCT, MIN_DIAS_SERIE, MOMENTO_BUENO, MOMENTO_MALO, MOVIDO_PCT,
    QUIETO_PCT, leer_ruta,
)


def _ruta(origen="Lima", destino="Cusco"):
    return SimpleNamespace(
        origin=SimpleNamespace(city=origen), destination=SimpleNamespace(city=destino)
    )


def _stats(mediana="250", p25="200"):
    return SimpleNamespace(median_30d=Decimal(mediana), p25_30d=Decimal(p25))


def _historia(precios):
    hoy = date(2026, 9, 5)
    return [
        SimpleNamespace(day=hoy - timedelta(days=i), price=Decimal(p) if p else None)
        for i, p in enumerate(precios)
    ]


def _fechas(baratas, total):
    return [
        {"verdict": SimpleNamespace(should_buy=i < baratas)} for i in range(total)
    ]


def _inversa(mediana):
    return SimpleNamespace(stats=SimpleNamespace(median_30d=Decimal(mediana)))


def claves(observaciones):
    return [o.clave for o in observaciones]


# --- movimiento ---------------------------------------------------------------

def test_una_ruta_plana_recibe_el_consejo_de_no_esperar():
    """Con 0% de variación, esperar no sirve. Es lo contrario de 'movido'."""
    historia = _historia(["250"] * 15)
    obs = leer_ruta(_ruta(), historia, [], _stats())
    assert claves(obs) == ["quieto"]
    assert "esperar" in obs[0].detalle.lower()


def test_una_ruta_volatil_recibe_el_consejo_de_vigilar():
    historia = _historia(["150", "280"] + ["200"] * 13)   # 130/250 = 52%
    obs = leer_ruta(_ruta(), historia, [], _stats())
    assert claves(obs) == ["movido"]
    assert "S/ 150" in obs[0].detalle and "S/ 280" in obs[0].detalle


def test_entre_umbrales_no_se_dice_nada_del_movimiento():
    """Una observación tibia es peor que ninguna: rellena sin informar."""
    amplitud = (QUIETO_PCT + MOVIDO_PCT) // 2          # 16% de 250 = 40 soles
    historia = _historia(["250", str(250 + amplitud * 250 // 100)] + ["250"] * 13)
    assert leer_ruta(_ruta(), historia, [], _stats()) == []


def test_con_pocos_dias_de_serie_no_se_lee_el_movimiento():
    """Diez observaciones de un precio que se movió mucho podrían ser azar."""
    historia = _historia(["150", "280"] + ["200"] * (MIN_DIAS_SERIE - 3))
    assert len(historia) < MIN_DIAS_SERIE
    assert leer_ruta(_ruta(), historia, [], _stats()) == []


# --- ida y vuelta -------------------------------------------------------------

def test_detecta_que_la_vuelta_es_mas_cara():
    obs = leer_ruta(_ruta("Lima", "Juliaca"), [], [], _stats("391"), inversa=_inversa("639"))
    assert claves(obs) == ["vuelta-cara"]
    assert "Juliaca" in obs[0].titular
    assert "63%" in obs[0].detalle


def test_detecta_que_la_ida_es_mas_cara():
    obs = leer_ruta(_ruta("Puerto Maldonado", "Cusco"), [], [], _stats("438"),
                    inversa=_inversa("261"))
    assert claves(obs) == ["ida-cara"]


def test_una_diferencia_pequena_con_la_vuelta_no_es_noticia():
    """Por debajo del umbral cambia solo por el día en que se mire."""
    poco = Decimal("250") * (100 + ASIMETRIA_PCT - 1) // 100
    assert leer_ruta(_ruta(), [], [], _stats("250"), inversa=_inversa(str(poco))) == []


def test_sin_ruta_inversa_no_se_compara():
    assert leer_ruta(_ruta(), [], [], _stats(), inversa=None) == []


# --- momento ------------------------------------------------------------------

def test_muchas_fechas_baratas_es_buen_momento():
    fechas = _fechas(baratas=35, total=45)
    assert 35 / 45 >= MOMENTO_BUENO
    obs = leer_ruta(_ruta(), [], fechas, _stats())
    assert claves(obs) == ["momento-bueno"]
    assert "35 de las 45" in obs[0].detalle


def test_pocas_fechas_baratas_es_mal_momento():
    fechas = _fechas(baratas=4, total=45)
    assert 4 / 45 <= MOMENTO_MALO
    assert claves(leer_ruta(_ruta(), [], fechas, _stats())) == ["momento-malo"]


def test_un_momento_normal_no_se_comenta():
    fechas = _fechas(baratas=20, total=45)
    assert leer_ruta(_ruta(), [], fechas, _stats()) == []


# --- lo que importa: perfiles distintos, párrafos distintos --------------------

def test_dos_rutas_con_perfil_distinto_no_dicen_lo_mismo():
    """El punto entero del módulo. Si esto falla, volvió a ser una plantilla."""
    plana = leer_ruta(_ruta(), _historia(["250"] * 15), _fechas(4, 45), _stats(),
                      inversa=_inversa("400"))
    movida = leer_ruta(_ruta(), _historia(["150", "280"] + ["200"] * 13),
                       _fechas(35, 45), _stats(), inversa=_inversa("250"))
    assert claves(plana) == ["quieto", "vuelta-cara", "momento-malo"]
    assert claves(movida) == ["movido", "momento-bueno"]
    assert not set(o.titular for o in plana) & set(o.titular for o in movida)


def test_nunca_mas_de_tres_observaciones():
    obs = leer_ruta(_ruta(), _historia(["250"] * 15), _fechas(4, 45), _stats(),
                    inversa=_inversa("400"))
    assert len(obs) <= lectura.MAX_OBSERVACIONES


def test_sin_nada_notable_devuelve_lista_vacia_y_no_inventa():
    """Una ruta sin particularidades no necesita que le fabriquemos una."""
    assert leer_ruta(_ruta(), [], [], None) == []
