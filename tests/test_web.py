"""Capa web pública: veredicto, ficha de ruta, sitemap y robots."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.flights.models import PriceSnapshot, Route, RouteStats
from apps.web import chart, queries
from apps.web.verdict import (ALTO, BUENO, CARO, EXCELENTE, MIN_TREND_DAYS, NORMAL,
                              SIN_DATOS, evaluate, evaluate_trend)


@pytest.fixture
def route(peru_airports):
    return Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True)


@pytest.fixture
def stats(route):
    """Ruta con histórico suficiente: p25 = 200, mediana = 260."""
    return RouteStats.objects.create(
        route=route, avg_30d=Decimal("270"), min_30d=Decimal("150"),
        p25_30d=Decimal("200"), median_30d=Decimal("260"), samples_count=40,
    )


def _snapshot(route, price, flight_offset=10):
    return PriceSnapshot.objects.create(
        route=route,
        flight_date=timezone.localdate() + timedelta(days=flight_offset),
        min_price_pen=Decimal(price), avg_price_pen=Decimal(price), offers_count=3,
    )


# --- veredicto (lógica pura) ------------------------------------------------

@pytest.mark.parametrize("precio,esperado", [
    ("179", EXCELENTE),   # <= p25 * 0.90 = 180
    ("195", BUENO),    # <= p25
    ("250", NORMAL),   # <= mediana
    ("300", ALTO),     # <= mediana * 1.20 = 312
    ("400", CARO),
])
def test_niveles_del_veredicto(stats, precio, esperado):
    assert evaluate(Decimal(precio), stats).level == esperado


def test_sin_muestras_suficientes_no_opina(stats):
    stats.samples_count = 3
    assert evaluate(Decimal("179"), stats).level == SIN_DATOS
    assert evaluate(Decimal("179"), stats).is_actionable is False


def test_sin_stats_ni_precio_no_revienta():
    assert evaluate(None, None).level == SIN_DATOS


def test_diferencia_contra_mediana_se_reporta(stats):
    v = evaluate(Decimal("130"), stats)
    assert v.vs_median_pct == -50
    assert v.should_buy is True


def test_umbral_coincide_con_el_motor_de_alertas(stats, settings):
    """La web no puede decir 'excelente' donde el bot no dispararía alerta."""
    limite = Decimal(stats.p25_30d) * settings.DEAL_P25_FACTOR
    assert evaluate(limite, stats).level == EXCELENTE
    assert evaluate(limite + Decimal("1"), stats).level != EXCELENTE


# --- consultas --------------------------------------------------------------

def test_precio_vigente_ignora_snapshots_viejos(route, stats):
    viejo = _snapshot(route, "100")
    PriceSnapshot.objects.filter(pk=viejo.pk).update(
        snapshot_at=timezone.now() - timedelta(days=5)
    )
    _snapshot(route, "300", flight_offset=12)
    assert queries.current_min_price(route) == Decimal("300")


def test_precio_vigente_ignora_fechas_pasadas(route, stats):
    _snapshot(route, "90", flight_offset=-3)
    _snapshot(route, "280", flight_offset=8)
    assert queries.current_min_price(route) == Decimal("280")


def test_minimo_historico_incluye_lo_purgable(route):
    antiguo = _snapshot(route, "120")
    PriceSnapshot.objects.filter(pk=antiguo.pk).update(
        snapshot_at=timezone.now() - timedelta(days=200)
    )
    _snapshot(route, "400", flight_offset=11)
    assert queries.all_time_low(route).min_price_pen == Decimal("120")


# --- gráfico ----------------------------------------------------------------

def test_grafico_vacio_con_un_solo_punto():
    serie = [queries.DayPrice(day=date(2026, 8, 1), price=Decimal("200"))]
    assert chart.build(serie).is_empty is True


def test_grafico_genera_coordenadas():
    serie = [
        queries.DayPrice(day=date(2026, 8, 1), price=Decimal("200")),
        queries.DayPrice(day=date(2026, 8, 2), price=Decimal("400")),
        queries.DayPrice(day=date(2026, 8, 3), price=Decimal("300")),
    ]
    c = chart.build(serie)
    assert c.is_empty is False
    assert len(c.points) == 3
    # El precio más alto queda arriba (y menor) que el más bajo.
    assert c.points[1][1] < c.points[0][1]


# --- vistas -----------------------------------------------------------------

def test_ficha_de_ruta_muestra_precio_y_veredicto(client, route, stats):
    _snapshot(route, "179")
    resp = client.get(reverse("web:route", args=["LIM", "CUZ"]))
    assert resp.status_code == 200
    cuerpo = resp.content.decode()
    assert "179" in cuerpo
    assert "Excelente precio" in cuerpo   # veredicto de esa fecha concreta
    assert "Cusco" in cuerpo


# --- tendencia: el mínimo de hoy contra los mínimos diarios -----------------

def _serie(precios):
    base = date(2026, 8, 1)
    return [Decimal(p) for p in precios]


def test_tendencia_calla_sin_dos_semanas_de_serie():
    v = evaluate_trend(Decimal("100"), _serie(["200"] * (MIN_TREND_DAYS - 1)))
    assert v.level == SIN_DATOS


def test_tendencia_detecta_caida_real():
    """Con la serie plana en 200, un mínimo de 120 hoy sí es una caída."""
    v = evaluate_trend(Decimal("120"), _serie(["200"] * MIN_TREND_DAYS))
    assert v.level == EXCELENTE
    assert v.vs_median_pct == -40


def test_tendencia_no_grita_excelente_en_dia_normal():
    """Regresión: comparar el mínimo entre fechas contra la distribución de
    todas las fechas daba 'excelente' siempre. Con la serie correcta, no."""
    v = evaluate_trend(Decimal("200"), _serie(["200"] * MIN_TREND_DAYS))
    assert v.level == NORMAL
    assert v.should_buy is False


def test_tendencia_marca_precio_alto():
    v = evaluate_trend(Decimal("260"), _serie(["200"] * MIN_TREND_DAYS))
    assert v.level in (ALTO, CARO)


def test_ficha_acepta_iata_en_minuscula(client, route, stats):
    _snapshot(route, "179")
    assert client.get(reverse("web:route", args=["lim", "cuz"])).status_code == 200


def test_ruta_inexistente_da_404(client, peru_airports):
    assert client.get("/vuelos/LIM-AQP/").status_code == 404


def test_portada_lista_rutas_publicadas(client, route, stats):
    _snapshot(route, "179")
    resp = client.get(reverse("web:home"))
    assert resp.status_code == 200
    cuerpo = resp.content.decode()
    # Se afirma el enlace y el precio, no la redaccion: el copy cambia con el
    # diseno y un test atado a la frase exacta solo genera ruido.
    assert "/vuelos/LIM-CUZ/" in cuerpo
    assert "179" in cuerpo


def test_portada_dice_cuando_ninguna_fecha_esta_barata(client, route, stats):
    _snapshot(route, "400")   # muy por encima de la mediana de 260
    resp = client.get(reverse("web:home"))
    assert "ninguna de 1 fechas" in resp.content.decode()


def test_portada_no_publica_rutas_sin_historico(client, route, stats):
    """Sin snapshots la ruta no existe para el público."""
    resp = client.get(reverse("web:home"))
    assert "LIM-CUZ" not in resp.content.decode()


def test_paginas_declaran_cache_para_el_borde(client, route, stats):
    _snapshot(route, "200")
    resp = client.get(reverse("web:route", args=["LIM", "CUZ"]))
    assert "s-maxage=1800" in resp["Cache-Control"]
    assert "public" in resp["Cache-Control"]


def test_sitemap_incluye_la_ruta(client, route, stats):
    _snapshot(route, "200")
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "/vuelos/LIM-CUZ/" in resp.content.decode()


def test_robots_declara_sitemap(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Sitemap:" in resp.content.decode()
    assert "Disallow: /healthz" in resp.content.decode()


# --- purga de caché en el borde --------------------------------------------

def test_purga_no_hace_nada_sin_token(settings):
    from apps.web.cloudflare import purge_everything

    settings.CLOUDFLARE_API_TOKEN = ""
    settings.CLOUDFLARE_ZONE_ID = ""
    assert purge_everything() is False


def test_purga_llama_a_cloudflare_con_el_token(settings, monkeypatch):
    import json as _json

    from apps.web import cloudflare

    settings.CLOUDFLARE_API_TOKEN = "tok"
    settings.CLOUDFLARE_ZONE_ID = "zona"
    capturado = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps({"success": True}).encode()

    def _fake_urlopen(req, timeout=None):
        capturado["url"] = req.full_url
        capturado["auth"] = req.headers.get("Authorization")
        capturado["body"] = _json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(cloudflare.urllib.request, "urlopen", _fake_urlopen)
    assert cloudflare.purge_everything() is True
    assert "zona" in capturado["url"]
    assert capturado["auth"] == "Bearer tok"
    assert capturado["body"] == {"purge_everything": True}


def test_purga_no_revienta_si_cloudflare_falla(settings, monkeypatch):
    from apps.web import cloudflare

    settings.CLOUDFLARE_API_TOKEN = "tok"
    settings.CLOUDFLARE_ZONE_ID = "zona"

    def _boom(req, timeout=None):
        raise cloudflare.urllib.error.URLError("sin red")

    monkeypatch.setattr(cloudflare.urllib.request, "urlopen", _boom)
    assert cloudflare.purge_everything() is False


# --- regresiones de los bloqueantes -----------------------------------------

def _ruta_publicada(origen, destino):
    """Ruta con estadísticas y un precio vigente, lista para publicarse."""
    from apps.flights.models import RouteStats

    r = Route.objects.create(origin_id=origen, destination_id=destino, is_monitored=True)
    RouteStats.objects.create(
        route=r, avg_30d=Decimal("270"), min_30d=Decimal("150"),
        p25_30d=Decimal("200"), median_30d=Decimal("260"), samples_count=40,
    )
    _snapshot(r, "179")
    return r


def test_portada_no_hace_una_consulta_por_ruta(client, peru_airports, django_assert_max_num_queries):
    """Regresión: la portada costaba 121 consultas y 22 s con 40 rutas.

    El tope subió de 6 a 9 al sumar los tres análisis de `insights`: una
    agregación cada uno, no una por ruta. Que el costo NO escale con las rutas
    es el invariante que de verdad protege esta página, y lo fija el test de
    abajo — un tope fijo se puede cumplir por accidente.
    """
    for destino in ("CUZ", "AQP", "PEM"):
        _ruta_publicada("LIM", destino)

    with django_assert_max_num_queries(9):
        assert client.get(reverse("web:home")).status_code == 200


def test_el_costo_de_la_portada_no_crece_con_las_rutas(client, peru_airports, django_assert_num_queries):
    """El invariante real: agregar rutas no agrega consultas.

    Un tope fijo se puede satisfacer por accidente; esto fija la forma del
    problema, que es lo que se rompió la primera vez.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for destino in ("CUZ", "AQP"):
        _ruta_publicada("LIM", destino)
    with CaptureQueriesContext(connection) as pocas:
        client.get(reverse("web:home"))

    for origen, destino in (("CUZ", "LIM"), ("AQP", "LIM"), ("PEM", "LIM"), ("LIM", "PEM")):
        _ruta_publicada(origen, destino)
    with CaptureQueriesContext(connection) as muchas:
        resp = client.get(reverse("web:home"))

    assert resp.status_code == 200
    assert len(muchas.captured_queries) == len(pocas.captured_queries), (
        f"{len(pocas.captured_queries)} consultas con 2 rutas y "
        f"{len(muchas.captured_queries)} con 6: el costo escala con las rutas"
    )


def test_ruta_monitoreada_sin_historico_da_404(client, peru_airports):
    """CHM y RIM no tienen servicio comercial: publicarlas sería una página vacía."""
    Route.objects.create(origin_id="LIM", destination_id="AQP", is_monitored=True)
    assert client.get("/vuelos/LIM-AQP/").status_code == 404


def test_la_pagina_es_un_documento_html_completo(client, route, stats):
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert cuerpo.startswith("<!doctype html>")
    assert '<html lang="es">' in cuerpo
    assert "<head>" in cuerpo and "<body>" in cuerpo
    assert "favicon.ico" in cuerpo
    assert "apple-touch-icon" in cuerpo


def test_404_usa_la_plantilla_propia(client, peru_airports):
    resp = client.get("/vuelos/LIM-AQP/")
    assert resp.status_code == 404
    assert "Esta página no existe" in resp.content.decode()


def test_500_es_autocontenida():
    """Django renderiza 500.html sin context processors: no puede heredar."""
    from django.template.loader import render_to_string

    html = render_to_string("500.html")
    assert html.startswith("<!doctype html>")
    assert "{{" not in html and "{%" not in html


# --- conversión, enlaces internos y Open Graph ------------------------------

def test_la_ficha_ofrece_alerta_con_la_ruta_en_el_enlace(client, route, stats, settings):
    """El enlace profundo lleva la ruta: sin eso el bot pierde el contexto."""
    settings.TELEGRAM_BOT_USERNAME = "Vuelosradar_bot"
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "https://t.me/Vuelosradar_bot?start=LIM-CUZ" in cuerpo
    assert "Avisarme por correo" in cuerpo


def test_sin_bot_configurado_no_se_muestra_el_cta(client, route, stats, settings):
    settings.TELEGRAM_BOT_USERNAME = ""
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "t.me" not in cuerpo


def test_la_ficha_enlaza_la_ruta_inversa(client, route, stats):
    """Regresión: las fichas eran hojas huérfanas y la autoridad no circulaba."""
    inversa = Route.objects.create(origin_id="CUZ", destination_id="LIM", is_monitored=True)
    _snapshot(inversa, "150")
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "/vuelos/CUZ-LIM/" in cuerpo


def test_solo_enlaza_rutas_publicadas(client, route, stats):
    """Una ruta sin histórico da 404: no se puede enlazar desde otra ficha."""
    Route.objects.create(origin_id="LIM", destination_id="AQP", is_monitored=True)
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "/vuelos/LIM-AQP/" not in cuerpo


def test_open_graph_con_imagen_absoluta(client, route, stats):
    """WhatsApp descarta `og:image` relativa: tiene que ser URL completa."""
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert 'property="og:image" content="http://testserver/static/web/og.png"' in cuerpo
    assert 'property="og:image:width" content="1200"' in cuerpo
    assert 'name="twitter:card" content="summary_large_image"' in cuerpo
    assert 'property="og:locale" content="es_PE"' in cuerpo


def test_open_graph_describe_la_ruta_concreta(client, route, stats):
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert 'property="og:title" content="Lima → Cusco desde S/ 179"' in cuerpo


# --- enlace profundo del lado del bot ---------------------------------------

@pytest.mark.parametrize("payload", ["", "hola", "LIM", "LIM-CUZ-AQP", "L1M-CUZ", "LIM-XXX"])
def test_payload_invalido_no_resuelve_ruta(route, payload):
    """Cualquiera puede escribir lo que quiera en un enlace de Telegram."""
    from asgiref.sync import async_to_sync

    from bot import db

    assert async_to_sync(db.get_route_from_deep_link)(payload) is None


def test_payload_valido_resuelve_la_ruta(route):
    from asgiref.sync import async_to_sync

    from bot import db

    encontrada = async_to_sync(db.get_route_from_deep_link)("lim-cuz")
    assert encontrada is not None
    assert encontrada.code == "LIM-CUZ"


def test_bienvenida_desde_la_web_menciona_la_ruta(route):
    from bot import formatting

    texto = formatting.welcome_from_route("Kevin", route)
    assert "Lima" in texto and "Cusco" in texto
    assert "/alerta LIM CUZ" in texto



# --- estáticos referenciados desde plantillas -------------------------------

def test_todo_static_referenciado_existe_en_disco():
    """Un `{% static %}` sin archivo tumba la página entera en producción.

    Los tests usan el storage plano, así que no lo detectarían; esto sí, y sin
    depender de haber corrido `collectstatic`.
    """
    import re
    from pathlib import Path

    from django.conf import settings

    plantillas = Path(settings.BASE_DIR) / "apps" / "web" / "templates"
    estaticos = Path(settings.BASE_DIR) / "apps" / "web" / "static"

    referencias = set()
    for tpl in plantillas.rglob("*.html"):
        referencias |= set(re.findall(r"{%\s*static\s+['\"]([^'\"]+)['\"]", tpl.read_text(encoding="utf-8")))

    assert referencias, "ninguna referencia a static encontrada: el test dejaría de proteger"
    faltantes = [r for r in sorted(referencias) if not (estaticos / r).exists()]
    assert not faltantes, f"referenciados en plantillas pero ausentes en disco: {faltantes}"


def test_ruta_con_snapshots_pero_sin_stats_no_rompe(client, peru_airports):
    """Una ruta puede tener histórico y no tener `RouteStats`.

    Pasa si todos sus snapshots son más viejos que la ventana de 30 días:
    `compute_route_stats` no le crea fila y el acceso a `route.stats` levanta
    `RelatedObjectDoesNotExist`. Sin este caso cubierto, la portada entera
    caería en 500 por una sola ruta rezagada.
    """
    ruta = Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True)
    _snapshot(ruta, "200")

    from apps.flights.models import RouteStats

    assert not RouteStats.objects.filter(route=ruta).exists()
    assert client.get(reverse("web:home")).status_code == 200

    resp = client.get(reverse("web:route", args=["LIM", "CUZ"]))
    assert resp.status_code == 200
    assert "Sin histórico suficiente" in resp.content.decode()


# --- tendencia: cuánto falta para poder opinar ------------------------------

def test_la_tendencia_dice_cuantos_dias_faltan():
    """Un 'no sé' con plazo es una promesa verificable; sin plazo es una excusa."""
    v = evaluate_trend(Decimal("120"), [Decimal("200")] * 7)
    assert v.level == SIN_DATOS
    assert v.samples == 7
    assert v.missing_days == MIN_TREND_DAYS - 7


def test_al_alcanzar_el_umbral_ya_no_falta_nada():
    v = evaluate_trend(Decimal("120"), [Decimal("200")] * MIN_TREND_DAYS)
    assert v.level != SIN_DATOS
    assert v.missing_days is None


def test_el_veredicto_por_fecha_no_habla_de_dias(stats):
    """`missing_days` es solo de la tendencia: la ficha ya opina por fecha."""
    assert evaluate(Decimal("179"), stats).missing_days is None


def test_la_ficha_muestra_el_plazo(client, route, stats):
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "nos faltan" in cuerpo and "de histórico" in cuerpo


# --- paginas por ciudad y migas ---------------------------------------------

def test_el_hub_lista_los_destinos_de_la_ciudad(client, peru_airports):
    for destino in ("CUZ", "AQP"):
        _ruta_publicada("LIM", destino)
    resp = client.get(reverse("web:hub", args=["lima"]))
    assert resp.status_code == 200
    cuerpo = resp.content.decode()
    assert "Vuelos desde" in cuerpo and "Lima" in cuerpo
    assert "/vuelos/LIM-CUZ/" in cuerpo and "/vuelos/LIM-AQP/" in cuerpo


def test_el_hub_no_incluye_rutas_de_otra_ciudad(client, peru_airports):
    _ruta_publicada("LIM", "CUZ")
    _ruta_publicada("AQP", "CUZ")
    cuerpo = client.get(reverse("web:hub", args=["lima"])).content.decode()
    assert "/vuelos/AQP-CUZ/" not in cuerpo


def test_ciudad_sin_rutas_publicadas_da_404(client, peru_airports):
    assert client.get("/vuelos/desde-arequipa/").status_code == 404


def test_la_url_del_hub_no_se_confunde_con_una_ruta(client, peru_airports):
    """Regresión: sin restringir la ficha a tres letras por lado,
    /vuelos/desde-lima/ entraría como origen 'desde' y destino 'lima'."""
    _ruta_publicada("LIM", "CUZ")
    assert client.get("/vuelos/desde-lima/").status_code == 200
    # Y una ficha con códigos que no son IATA ni siquiera enruta.
    assert client.get("/vuelos/abcd-efgh/").status_code == 404


def test_la_ficha_trae_migas_hacia_el_hub(client, route, stats):
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "/vuelos/desde-lima/" in cuerpo
    assert "BreadcrumbList" in cuerpo


def test_el_sitemap_incluye_las_ciudades(client, peru_airports):
    _ruta_publicada("LIM", "CUZ")
    cuerpo = client.get("/sitemap.xml").content.decode()
    assert "/vuelos/desde-lima/" in cuerpo
    assert "/vuelos/LIM-CUZ/" in cuerpo


def test_la_portada_enlaza_las_ciudades(client, peru_airports):
    _ruta_publicada("LIM", "CUZ")
    assert "/vuelos/desde-lima/" in client.get(reverse("web:home")).content.decode()


def test_el_grafico_expone_el_largo_para_animarse():
    """Sin el largo exacto, `stroke-dasharray` necesitaria medirse con JS."""
    serie = [
        queries.DayPrice(day=date(2026, 8, d), price=Decimal(p))
        for d, p in [(1, "200"), (2, "400"), (3, "300")]
    ]
    c = chart.build(serie)
    assert c.length > 0
    assert chart.build(serie[:1]).length == 0


def test_el_svg_usa_punto_decimal_no_coma(client, route, stats):
    """Regresión: con LANGUAGE_CODE='es' Django escribía cx="712,0".

    Una coma es inválida como coordenada SVG y como valor CSS: el punto saltaba
    al origen y `stroke-dasharray` quedaba sin aplicar.
    """
    from datetime import timedelta

    from django.utils import timezone

    for i in range(4):
        s = _snapshot(route, str(200 + i * 30), flight_offset=5 + i)
        PriceSnapshot.objects.filter(pk=s.pk).update(
            snapshot_at=timezone.now() - timedelta(days=3 - i * 0.5)
        )
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    import re

    for atributo in re.findall(r'c[xy]="([^"]+)"', cuerpo):
        assert "," not in atributo, f'coordenada localizada: {atributo}'
    for largo in re.findall(r'--len:([^"]+)"', cuerpo):
        assert "," not in largo, f'--len localizado: {largo}'


# --- paginas legales --------------------------------------------------------

@pytest.mark.parametrize("url,titulo", [
    ("/terminos/", "Términos"),
    ("/privacidad/", "Privacidad"),
])
def test_las_paginas_legales_responden(client, url, titulo):
    resp = client.get(url)
    assert resp.status_code == 200
    assert titulo in resp.content.decode()


def test_la_privacidad_declara_el_envio_a_proveedores_de_ia(client):
    """Es la divulgación que no se puede omitir: el texto del usuario sale."""
    cuerpo = client.get("/privacidad/").content.decode()
    assert "Anthropic" in cuerpo
    assert "/vuelo LIM CUZ" in cuerpo  # la alternativa que no pasa por un modelo


def test_el_pie_enlaza_lo_legal_desde_cualquier_pagina(client, route, stats):
    _snapshot(route, "179")
    for url in ("/", reverse("web:route", args=["LIM", "CUZ"])):
        cuerpo = client.get(url).content.decode()
        assert "/terminos/" in cuerpo and "/privacidad/" in cuerpo


def test_las_legales_estan_en_el_sitemap(client, db):
    # El sitemap tambien recorre rutas y ciudades, asi que necesita la base.
    cuerpo = client.get("/sitemap.xml").content.decode()
    assert "/terminos/" in cuerpo and "/privacidad/" in cuerpo


def test_una_pagina_legal_inventada_da_404(client):
    from django.urls import Resolver404, resolve

    with pytest.raises(Resolver404):
        resolve("/legal-inventado/")


# --- el boton "Notificame" crea la alerta ------------------------------------

def test_la_ficha_ofrece_los_dos_canales(client, route, stats, settings):
    """Telegram tiene ~6% de penetracion en Peru: el correo no puede faltar."""
    settings.TELEGRAM_BOT_USERNAME = "Vuelosradar_bot"
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "/aviso/nuevo/?ruta=LIM-CUZ" in cuerpo
    assert "https://t.me/Vuelosradar_bot?start=LIM-CUZ" in cuerpo
    assert "se crea sola" in cuerpo


def test_el_mensaje_confirma_la_alerta_creada(route):
    from bot import formatting

    texto = formatting.welcome_from_route("Kevin", route, {"status": "ok", "created": True, "remaining": 1})
    assert "te aviso" in texto
    assert "/misalertas" in texto
    # Ya no le pide escribir el comando: eso era repetir una decisión ya tomada.
    assert "/alerta LIM CUZ" not in texto


def test_el_mensaje_avisa_si_no_queda_cupo(route):
    from bot import formatting

    texto = formatting.welcome_from_route("Kevin", route, {"status": "limit_reached", "limit": 2})
    assert "2 alertas activas" in texto
    assert "/misalertas" in texto


def test_sin_alerta_cae_al_comando_manual(route):
    """Si la creación falló por algo inesperado, el usuario igual puede seguir."""
    from bot import formatting

    texto = formatting.welcome_from_route("Kevin", route, None)
    assert "/alerta LIM CUZ" in texto


# --- calendario en grilla ----------------------------------------------------

def test_la_grilla_agrupa_por_semana_de_lunes_a_domingo():
    from apps.web import calendar_grid
    from apps.web.verdict import evaluate

    # 2026-08-27 es jueves; 2026-08-31 es lunes de la semana siguiente.
    fechas = [
        {"day": date(2026, 8, d), "price": Decimal("200"), "verdict": evaluate(None, None)}
        for d in (27, 28, 31)
    ]
    semanas = calendar_grid.build(fechas)
    assert len(semanas) == 2
    # Jueves y viernes ocupan las posiciones 3 y 4; el resto de la semana, vacío.
    assert [i for i, c in enumerate(semanas[0].dias) if c] == [3, 4]
    assert [i for i, c in enumerate(semanas[1].dias) if c] == [0]


def test_la_grilla_vacia_no_revienta():
    from apps.web import calendar_grid

    assert calendar_grid.build([]) == []


def test_la_ficha_muestra_la_grilla_y_no_45_filas(client, route, stats):
    """Regresión: la tabla de 45 filas eran ~7.000 px de scroll en un teléfono."""
    from datetime import timedelta as td

    for i in range(20):
        _snapshot(route, str(150 + i * 5), flight_offset=3 + i)
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert 'class="cal-fila"' in cuerpo
    assert "Calendario de precios" in cuerpo
    # La cabecera de dias aparece una sola vez, no por semana.
    assert cuerpo.count('class="cal-cab"') == 1


# --- pie, botón flotante y publicidad ---------------------------------------

def test_el_pie_acredita_a_quien_construyo_el_sitio(client, route, stats):
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "Star Insights IT by SISAC" in cuerpo
    assert "https://sisac.pe/" in cuerpo


def test_el_credito_del_pie_sale_por_configuracion(client, route, stats, settings):
    """Sin BUILDER_NAME el pie no inventa un crédito vacío."""
    settings.BUILDER_NAME = ""
    cuerpo = client.get(reverse("web:home")).content.decode()
    # `foot-by` a secas también matchea la regla CSS: hay que mirar el marcado.
    assert 'class="foot-by"' not in cuerpo


def test_sin_publicidad_no_hay_ads_txt(client, settings):
    """Un ads.txt con el `pub-` vacío sería una declaración falsa sobre quién
    puede vender esta publicidad."""
    settings.ADSENSE_CLIENT = ""
    assert client.get("/ads.txt").status_code == 404


def test_ads_txt_declara_al_editor_sin_el_prefijo_ca(client, settings):
    settings.ADSENSE_CLIENT = "ca-pub-0000000000000000"
    resp = client.get("/ads.txt")
    assert resp.status_code == 200
    assert resp.content.decode() == (
        "google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0\n"
    )


def test_la_verificacion_de_search_console_es_opcional(client, route, stats, settings):
    settings.GOOGLE_SITE_VERIFICATION = ""
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "google-site-verification" not in cuerpo

    settings.GOOGLE_SITE_VERIFICATION = "abc123"
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert '<meta name="google-site-verification" content="abc123">' in cuerpo


def test_el_pie_se_separa_del_contenido(client, route, stats):
    """Regresión: `.wrap` es una clase y `footer` un elemento, así que el
    `margin:0 auto;padding:0 1.5rem` de `.wrap` ganaba y dejaba el pie pegado a
    la última tarjeta. El selector tiene que llevar las dos cosas."""
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "footer.wrap{" in cuerpo
    # Un `footer{` a secas volvería a perder contra `.wrap`.
    assert "\nfooter{" not in cuerpo


def test_el_logo_del_constructor_es_opcional(client, route, stats, settings):
    """Sin BUILDER_LOGO no se referencia un estático que puede no existir:
    `{% static %}` con manifiesto revienta en producción si falta el archivo."""
    settings.BUILDER_LOGO = ""
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert 'class="foot-by"' in cuerpo
    marca_pie = cuerpo.split('class="foot-by"')[1].split("</span>")[0]
    assert "<img" not in marca_pie


def test_con_logo_configurado_el_pie_lo_muestra(client, route, stats, settings):
    settings.BUILDER_LOGO = "web/sisac-logo.png"
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "sisac-logo.png" in cuerpo


def test_el_pie_mantiene_la_promesa_que_sostiene_la_marca(client, route, stats):
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "No vendemos pasajes ni cobramos comisión." in cuerpo


def test_el_boton_flotante_lleva_la_ruta_al_bot(client, route, stats, settings):
    """Abrir el bot en blanco desperdicia la única conversión del embudo."""
    settings.TELEGRAM_BOT_USERNAME = "Vuelosradar_bot"
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert 'class="fab"' in cuerpo
    assert "https://t.me/Vuelosradar_bot?start=LIM-CUZ" in cuerpo


def test_sin_bot_configurado_no_hay_boton_flotante(client, route, stats, settings):
    settings.TELEGRAM_BOT_USERNAME = ""
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert 'class="fab"' not in cuerpo


def test_sin_id_de_editor_no_se_pide_nada_a_google(client, route, stats, settings):
    """El sitio sigue en cero peticiones a terceros mientras no haya AdSense."""
    settings.ADSENSE_CLIENT = ""
    settings.ADSENSE_SLOT_ROUTE = "1234567890"
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "googlesyndication" not in cuerpo
    assert 'class="ad"' not in cuerpo


def test_sin_slot_no_se_dibuja_un_hueco_vacio(client, route, stats, settings):
    """Un contenedor sin anuncio desplaza el contenido para nada."""
    settings.ADSENSE_CLIENT = "ca-pub-0000000000000000"
    settings.ADSENSE_SLOT_ROUTE = ""
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert 'class="ad"' not in cuerpo


def test_con_editor_y_slot_el_anuncio_se_rotula(client, route, stats, settings):
    """Las políticas de AdSense exigen distinguir el anuncio del contenido, y
    acá confundirlo con el veredicto costaría más de lo que paga."""
    settings.ADSENSE_CLIENT = "ca-pub-0000000000000000"
    settings.ADSENSE_SLOT_ROUTE = "1234567890"
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert 'class="ad"' in cuerpo
    assert "Publicidad" in cuerpo
    assert 'data-ad-slot="1234567890"' in cuerpo
    assert "googlesyndication" in cuerpo
