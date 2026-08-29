"""Piezas de producción: registry por flags, verificación, throttle y salud."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

FECHA = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


# ------------------------------------------------------ registry por flags
def test_los_scrapers_directos_estan_apagados_por_defecto(settings):
    from apps.scraping.providers.registry import get_direct_providers

    settings.ENABLE_SKY_SCRAPER = False
    settings.ENABLE_JETSMART_SCRAPER = False
    assert get_direct_providers() == []


def test_el_flag_habilita_sky(settings):
    from apps.scraping.providers.registry import get_direct_providers

    settings.ENABLE_SKY_SCRAPER = True
    settings.ENABLE_JETSMART_SCRAPER = False
    assert [p.source_name for p in get_direct_providers()] == ["sky"]


def test_el_barrido_masivo_nunca_usa_los_directos(settings):
    """Son demasiado pesados para 1.300 consultas por corrida."""
    from apps.scraping.providers.registry import get_active_providers

    settings.ENABLE_SKY_SCRAPER = True
    settings.ENABLE_JETSMART_SCRAPER = True
    assert [p.source_name for p in get_active_providers()] == ["google_flights"]


@pytest.mark.django_db
def test_solo_las_rutas_marcadas_pagan_el_costo_de_los_directos(peru_airports, settings):
    from apps.flights.models import Route
    from apps.scraping.providers.registry import get_providers_for_route

    settings.ENABLE_SKY_SCRAPER = True

    normal = Route.objects.create(origin_id="LIM", destination_id="CUZ")
    assert [p.source_name for p in get_providers_for_route(normal)] == ["google_flights"]

    marcada = Route.objects.create(
        origin_id="LIM", destination_id="AQP", use_direct_scrapers=True
    )
    assert [p.source_name for p in get_providers_for_route(marcada)] == [
        "google_flights", "sky",
    ]


def test_sin_ruta_se_usa_la_lista_base(settings):
    """Los tramos sueltos de una conexión no traen ruta."""
    from apps.scraping.providers.registry import get_providers_for_route

    settings.ENABLE_SKY_SCRAPER = True
    assert [p.source_name for p in get_providers_for_route(None)] == ["google_flights"]


# --------------------------------------------------- verificación de precio
def fake_direct(price):
    from apps.scraping.providers.base import RawFlightOffer

    class Fake:
        source_name = "sky"

        def search(self, origin, dest, flight_date):
            if price is None:
                return []
            return [
                RawFlightOffer(
                    origin=origin, destination=dest, search_date=flight_date,
                    price_pen=Decimal(price), source="sky", airline="Sky Airline",
                )
            ]

    return patch(
        "apps.scraping.verification.get_direct_providers", return_value=[Fake()]
    )


def test_sin_scrapers_directos_el_precio_pasa_sin_verificar():
    from apps.scraping.verification import verify_price

    with patch("apps.scraping.verification.get_direct_providers", return_value=[]):
        r = verify_price("LIM", "CUZ", FECHA, Decimal("200"))

    assert r.verified is False
    assert r.price == Decimal("200")
    assert r.source == "google_flights"


def test_precio_confirmado_se_mantiene(settings):
    from apps.scraping.verification import verify_price

    settings.ALERT_PRICE_DISCREPANCY_PCT = Decimal("10")
    with fake_direct("205"):
        r = verify_price("LIM", "CUZ", FECHA, Decimal("200"))

    assert r.verified is True
    assert r.price == Decimal("200"), "diferencia de 2.5%, gana el original"
    assert r.is_significant is False


def test_discrepancia_grande_gana_el_directo(settings):
    from apps.scraping.verification import verify_price

    settings.ALERT_PRICE_DISCREPANCY_PCT = Decimal("10")
    with fake_direct("260"):
        r = verify_price("LIM", "CUZ", FECHA, Decimal("200"))

    assert r.verified is True
    assert r.price == Decimal("260"), "30% de diferencia, gana la aerolínea"
    assert r.is_significant is True
    assert r.discrepancy_pct == Decimal("30.0")


def test_si_el_scraper_directo_no_devuelve_nada_no_bloquea():
    from apps.scraping.verification import verify_price

    with fake_direct(None):
        r = verify_price("LIM", "CUZ", FECHA, Decimal("200"))

    assert r.verified is False
    assert r.price == Decimal("200")


# --------------------------------------------------------- throttle global
def test_el_semaforo_deja_pasar_hasta_el_techo(settings):
    from bot import throttle

    settings.BOT_GLOBAL_SEARCH_LIMIT = 3
    throttle.reset()

    for _ in range(3):
        throttle._acquire_or_raise()

    with pytest.raises(throttle.NoSlotAvailable):
        throttle._acquire_or_raise()

    assert throttle.in_flight() == 3


def test_liberar_un_slot_deja_entrar_al_siguiente(settings):
    from bot import throttle

    settings.BOT_GLOBAL_SEARCH_LIMIT = 1
    throttle.reset()

    throttle._acquire_or_raise()
    with pytest.raises(throttle.NoSlotAvailable):
        throttle._acquire_or_raise()

    throttle.release_slot()
    throttle._acquire_or_raise()   # ahora sí


def test_el_contexto_libera_aunque_el_bloque_falle(settings):
    from bot import throttle

    settings.BOT_GLOBAL_SEARCH_LIMIT = 1
    throttle.reset()

    with pytest.raises(RuntimeError):
        with throttle.search_slot():
            raise RuntimeError("la búsqueda explotó")

    assert throttle.in_flight() == 0


def test_si_redis_se_cae_el_bot_sigue_funcionando(settings):
    """Un contador inaccesible no debe dejar a todos sin buscar."""
    from bot import throttle

    settings.BOT_GLOBAL_SEARCH_LIMIT = 1
    with patch.object(throttle.cache, "add", side_effect=ConnectionError("redis caído")):
        throttle._acquire_or_raise()   # no lanza


# ------------------------------------------------------------ healthcheck
@pytest.mark.django_db
def test_healthcheck_ok_con_snapshots_recientes(peru_airports, settings):
    from apps.flights.models import PriceSnapshot, Route
    from apps.scraping.maintenance import system_healthcheck

    settings.HEALTH_MAX_SNAPSHOT_AGE_HOURS = 8
    ruta = Route.objects.create(origin_id="LIM", destination_id="CUZ")
    PriceSnapshot.objects.create(
        route=ruta, flight_date=FECHA, min_price_pen=Decimal("200"),
        avg_price_pen=Decimal("240"), offers_count=5,
    )

    with patch("apps.scraping.maintenance.send_admin_alert") as aviso:
        resultado = system_healthcheck.apply().get()

    assert resultado["status"] == "ok"
    aviso.assert_not_called()


@pytest.mark.django_db
def test_el_hueco_normal_entre_barridos_no_dispara_la_alerta(peru_airports, settings):
    """Regresión: el barrido corre 06:00 y 18:00 y tarda ~2h, así que el último
    snapshot de la noche entra ~20:00 y el siguiente recién a las 06:00 — 10h
    de hueco NORMAL. Con el tope de 8h que había, la alerta saltaba todas las
    madrugadas, y una alerta que grita cada noche entrena a ignorarla.
    """
    from django.utils import timezone

    from apps.flights.models import PriceSnapshot, Route
    from apps.scraping.maintenance import system_healthcheck

    ruta = Route.objects.create(origin_id="LIM", destination_id="CUZ")
    snap = PriceSnapshot.objects.create(
        route=ruta, flight_date=FECHA, min_price_pen=Decimal("200"),
        avg_price_pen=Decimal("240"), offers_count=5,
    )
    # 11 horas: dentro de lo normal entre dos barridos.
    PriceSnapshot.objects.filter(pk=snap.pk).update(
        snapshot_at=timezone.now() - timedelta(hours=11)
    )

    with patch("apps.scraping.maintenance.send_admin_alert") as aviso:
        resultado = system_healthcheck.apply().get()

    assert resultado["status"] == "ok"
    aviso.assert_not_called()


@pytest.mark.django_db
def test_el_mismo_problema_no_se_avisa_dos_veces(peru_airports, settings):
    """El chequeo corre cada 30 min: sin tregua, un problema de un día manda 48
    mensajes idénticos y deja de leerse justo cuando hay algo que leer."""
    from django.core.cache import cache

    from apps.scraping.maintenance import system_healthcheck

    cache.clear()   # sin snapshots en la base, el problema es real

    with patch("apps.scraping.maintenance.send_admin_alert") as aviso:
        primera = system_healthcheck.apply().get()
        segunda = system_healthcheck.apply().get()

    assert primera["notified"] is True
    assert segunda["notified"] is False
    assert aviso.call_count == 1


@pytest.mark.django_db
def test_al_volver_a_la_normalidad_se_limpia_la_tregua(peru_airports, settings):
    """Si el problema reaparece mañana hay que enterarse enseguida, no seis
    horas después."""
    from django.core.cache import cache

    from apps.flights.models import PriceSnapshot, Route
    from apps.scraping.maintenance import _CLAVE_AVISO, system_healthcheck

    cache.clear()
    with patch("apps.scraping.maintenance.send_admin_alert"):
        system_healthcheck.apply().get()
    assert cache.get(_CLAVE_AVISO) is not None

    ruta = Route.objects.create(origin_id="LIM", destination_id="CUZ")
    PriceSnapshot.objects.create(
        route=ruta, flight_date=FECHA, min_price_pen=Decimal("200"),
        avg_price_pen=Decimal("240"), offers_count=5,
    )
    with patch("apps.scraping.maintenance.send_admin_alert"):
        assert system_healthcheck.apply().get()["status"] == "ok"

    assert cache.get(_CLAVE_AVISO) is None


@pytest.mark.django_db
def test_un_problema_nuevo_se_avisa_aunque_el_anterior_siga(peru_airports, settings):
    """Callar un síntoma distinto porque otro sigue abierto sería esconder
    información."""
    from django.core.cache import cache

    from apps.scraping.maintenance import _avisar_una_vez

    cache.clear()
    with patch("apps.scraping.maintenance.send_admin_alert") as aviso:
        assert _avisar_una_vez(["el histórico se congeló"]) is True
        assert _avisar_una_vez(["el histórico se congeló"]) is False
        assert _avisar_una_vez(
            ["el histórico se congeló", "la fuente está pausada"]) is True

    assert aviso.call_count == 2


@pytest.mark.django_db
def test_healthcheck_avisa_si_el_histórico_se_congeló(peru_airports, settings):
    from apps.flights.models import PriceSnapshot, Route
    from apps.scraping.maintenance import system_healthcheck

    settings.HEALTH_MAX_SNAPSHOT_AGE_HOURS = 8
    ruta = Route.objects.create(origin_id="LIM", destination_id="CUZ")
    snap = PriceSnapshot.objects.create(
        route=ruta, flight_date=FECHA, min_price_pen=Decimal("200"),
        avg_price_pen=Decimal("240"), offers_count=5,
    )
    PriceSnapshot.objects.filter(pk=snap.pk).update(
        snapshot_at=timezone.now() - timedelta(hours=20)
    )

    with patch("apps.scraping.maintenance.send_admin_alert") as aviso:
        resultado = system_healthcheck.apply().get()

    assert resultado["status"] == "degraded"
    assert any("20h" in p for p in resultado["issues"])
    aviso.assert_called_once()


@pytest.mark.django_db
def test_healthcheck_avisa_si_la_fuente_esta_pausada(peru_airports, settings):
    from apps.flights.models import PriceSnapshot, Route
    from apps.scraping import ratelimit
    from apps.scraping.maintenance import system_healthcheck

    ruta = Route.objects.create(origin_id="LIM", destination_id="CUZ")
    PriceSnapshot.objects.create(
        route=ruta, flight_date=FECHA, min_price_pen=Decimal("200"),
        avg_price_pen=Decimal("240"), offers_count=5,
    )
    ratelimit.pause("google_flights", 1800)

    with patch("apps.scraping.maintenance.send_admin_alert") as aviso:
        resultado = system_healthcheck.apply().get()

    assert resultado["status"] == "degraded"
    assert any("pausada" in p for p in resultado["issues"])
    aviso.assert_called_once()


@pytest.mark.django_db
def test_healthcheck_avisa_si_no_hay_ningun_snapshot():
    from apps.scraping.maintenance import system_healthcheck

    with patch("apps.scraping.maintenance.send_admin_alert") as aviso:
        resultado = system_healthcheck.apply().get()

    assert resultado["status"] == "degraded"
    aviso.assert_called_once()


# ----------------------------------------------------------------- backup
@pytest.mark.django_db
def test_backup_sin_database_url_no_hace_nada(settings):
    from apps.scraping.maintenance import backup_database

    settings.DATABASE_URL = ""
    assert backup_database.apply().get()["status"] == "skipped"


@pytest.mark.django_db
def test_backup_reporta_si_falta_pg_dump(settings, tmp_path):
    from apps.scraping.maintenance import backup_database

    settings.DATABASE_URL = "postgresql://u:p@host:5432/db"
    settings.BACKUP_DIR = str(tmp_path)
    settings.PG_DUMP_PATH = "pg_dump_que_no_existe"

    assert backup_database.apply().get()["reason"] == "pg_dump_missing"


# ------------------------------------------------------ parser de JetSmart
@pytest.mark.parametrize(
    ("dia", "esperado"),
    [(6, Decimal("141.16")), (7, Decimal("134.44")), (99, None)],
)
def test_lectura_del_calendario_de_jetsmart(dia, esperado):
    from apps.scraping.providers.jetsmart import price_for_day

    calendario = "5\nS/134.44\n6\nS/141.16\n7\nS/134.44\nMejor precio\n8\nS/134.44"
    assert price_for_day(calendario, dia) == esperado


# ------------------------------- fallo de sistema vs ruta sin vuelos
def test_una_busqueda_rota_devuelve_none_no_lista_vacia():
    """Regresión: una migración sin aplicar hacía que el bot dijera
    "no encontré vuelos" cuando en realidad la base estaba caída."""
    from bot.db import _search_sync

    with patch("apps.scraping.services.search_and_store",
               side_effect=RuntimeError("column ... does not exist")):
        assert _search_sync("LIM", "CUZ", FECHA) is None


@pytest.mark.django_db
def test_una_ruta_sin_vuelos_devuelve_lista_vacia():
    from bot.db import _search_sync

    with patch("apps.scraping.services.search_and_store", return_value=[]):
        assert _search_sync("LIM", "ANS", FECHA) == []


def test_el_mensaje_de_error_no_dice_que_no_hay_vuelos():
    from bot import formatting

    texto = formatting.system_error_message()
    assert "no es que no haya" in texto
    assert "No encontré vuelos" not in texto
