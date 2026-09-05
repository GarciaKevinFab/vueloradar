"""Capa web pública: veredicto, ficha de ruta, sitemap y robots."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.flights.models import PriceSnapshot, Route, RouteStats
from apps.web import chart, context_processors, queries
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
    """Un enlace tecleado a mano tiene que llegar, aunque sea por redirección."""
    _snapshot(route, "179")
    assert client.get(reverse("web:route", args=["lim", "cuz"]), follow=True).status_code == 200


def test_la_ficha_en_otra_caja_redirige_a_la_canonica(client, route, stats):
    """Sin esto la misma ficha vive en 64 URLs, cada una canónica de sí misma.

    El `<link rel="canonical">` se arma con la URL pedida, así que aceptar
    cualquier combinación de mayúsculas con un 200 le entrega a Google 2^6
    duplicados por ruta sin señal de cuál vale. La canónica es MAYÚSCULAS: es
    lo que emiten los `{% url %}`, el sitemap y los nombres de las imágenes OG.
    """
    _snapshot(route, "179")
    for pedida in ("lim-cuz", "Lim-Cuz", "lIM-cUz", "lim-CUZ"):
        resp = client.get(f"/vuelos/{pedida}/")
        assert resp.status_code == 301, f"{pedida} no redirigió"
        assert resp["Location"] == "/vuelos/LIM-CUZ/", f"{pedida} fue a {resp['Location']}"


def test_la_forma_canonica_no_redirige(client, route, stats):
    """Un redirect sobre la canónica sería un bucle."""
    _snapshot(route, "179")
    assert client.get("/vuelos/LIM-CUZ/").status_code == 200


def test_el_canonical_solo_puede_ser_la_forma_canonica(client, route, stats):
    """El canonical se arma con `request.path`, así que lo fija la redirección.

    Es la señal que Google lee para consolidar duplicados: si la URL pedida
    llega intacta a la plantilla, cada caja se declara canónica de sí misma.
    """
    _snapshot(route, "179")
    directa = client.get("/vuelos/LIM-CUZ/").content.decode()
    assert 'rel="canonical" href="http://testserver/vuelos/LIM-CUZ/"' in directa

    # Y llegando por redirección, el canonical tiene que ser el mismo.
    redirigida = client.get("/vuelos/lim-cuz/", follow=True).content.decode()
    assert 'rel="canonical" href="http://testserver/vuelos/LIM-CUZ/"' in redirigida
    assert "/vuelos/lim-cuz/" not in redirigida


def test_una_ruta_inexistente_en_otra_caja_no_se_traga_el_404(client, peru_airports):
    """La redirección va primero, pero el 404 tiene que seguir llegando."""
    assert client.get("/vuelos/lim-aqp/", follow=True).status_code == 404


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
    # El invariante es que sea ABSOLUTA, no cuál es: desde que cada página
    # comparte su propia imagen, fijar el archivo concreto acá solo ataría el
    # test a un detalle que va a cambiar.
    assert 'property="og:image" content="http://testserver/static/web/' in cuerpo
    assert 'content="/static/' not in cuerpo
    assert 'property="og:image:width" content="1200"' in cuerpo
    assert 'name="twitter:card" content="summary_large_image"' in cuerpo
    assert 'property="og:locale" content="es_PE"' in cuerpo


def test_cada_ruta_comparte_su_propia_imagen(client, route, stats):
    """Antes todas las páginas compartían la misma, así que un enlace a
    Lima-Cusco se veía igual que uno a la portada y no decía nada."""
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "web/og/LIM-CUZ.png" in cuerpo


def test_sin_imagen_generada_se_cae_a_la_generica(client, peru_airports):
    """`{% static %}` con manifiesto revienta si el archivo no está: preguntar
    antes es la diferencia entre una página y un 500."""
    from apps.web import og_images

    assert "LIM-PEM" in og_images.RUTAS          # las generadas están
    assert "ZZZ-YYY" not in og_images.RUTAS      # y una inventada no


def test_la_ficha_ofrece_compartir_por_whatsapp(client, route, stats):
    """WhatsApp tiene ~94% de penetración en Perú: es el único canal por el que
    este sitio puede difundirse sin presupuesto."""
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "wa.me/?text=" in cuerpo
    # La URL viaja dentro del parámetro `text`, así que sus barras van
    # codificadas o WhatsApp corta el mensaje.
    assert "%2Fvuelos%2FLIM-CUZ%2F" in cuerpo


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


def test_el_sitemap_incluye_las_ciudades_con_varios_destinos(client, peru_airports):
    _ruta_publicada("LIM", "CUZ")
    _ruta_publicada("LIM", "AQP")
    cuerpo = client.get("/sitemap.xml").content.decode()
    assert "/vuelos/desde-lima/" in cuerpo
    assert "/vuelos/LIM-CUZ/" in cuerpo


def test_una_ciudad_con_un_solo_destino_no_va_al_sitemap(client, peru_airports):
    """Su hub muestra una fila, y esa ficha ya existe con más información.

    Trece de las dieciocho ciudades estaban así: publicarlos era pedirle a
    Google que indexara duplicados empobrecidos de las fichas propias. La
    página sigue existiendo para quien llegue desde el selector de la portada,
    pero no compite en el índice.
    """
    _ruta_publicada("LIM", "CUZ")
    cuerpo = client.get("/sitemap.xml").content.decode()
    assert "/vuelos/desde-lima/" not in cuerpo
    assert "/vuelos/LIM-CUZ/" in cuerpo, "la ficha sí tiene que seguir publicada"


def test_el_hub_flaco_se_marca_noindex(client, peru_airports):
    """No basta con sacarlo del sitemap: Google llega igual por los enlaces."""
    _ruta_publicada("LIM", "CUZ")
    flaco = client.get(reverse("web:hub", args=["lima"])).content.decode()
    assert 'name="robots" content="noindex,follow"' in flaco

    _ruta_publicada("LIM", "AQP")
    gordo = client.get(reverse("web:hub", args=["lima"])).content.decode()
    assert "noindex" not in gordo, "con dos destinos el hub vuelve a indexarse solo"


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


def _sitemap_entradas(client):
    """{url: {"lastmod": …, "changefreq": …}} a partir del sitemap servido."""
    import xml.etree.ElementTree as ET
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    raiz = ET.fromstring(client.get("/sitemap.xml").content)
    salida = {}
    for u in raiz.findall("s:url", ns):
        def dato(tag):
            nodo = u.find(f"s:{tag}", ns)
            return nodo.text if nodo is not None else None
        salida[dato("loc")] = {"lastmod": dato("lastmod"), "changefreq": dato("changefreq")}
    return salida


def test_las_paginas_que_cambian_con_el_barrido_declaran_lastmod(client, route, stats):
    """`changefreq` sin `lastmod` es una intención; la fecha es la señal real.

    Portada, buscador, cuándo-comprar y los hubs se rehacen con cada barrido
    igual que las fichas, pero sólo las fichas traían fecha: le pedíamos a
    Googlebot que volviera a diario sin darle con qué comprobarlo.
    """
    _snapshot(route, "179")
    # El hub necesita dos destinos para entrar al sitemap; `route` aporta uno.
    _ruta_publicada("LIM", "AQP")
    entradas = _sitemap_entradas(client)
    for camino in ("/", "/buscar/", "/cuando-comprar/", "/vuelos/desde-lima/"):
        url = next(u for u in entradas if u.endswith(camino))
        assert entradas[url]["lastmod"], f"{camino} sin lastmod"
        assert entradas[url]["changefreq"] == "daily"


def test_las_legales_no_dicen_que_cambian_a_diario(client, route, stats):
    """Términos y privacidad son texto fijo: no se rastrean como los precios.

    Declararlas diarias gasta rastreo en páginas que llevan meses iguales y le
    resta credibilidad a la señal en las que sí cambian.
    """
    _snapshot(route, "179")
    entradas = _sitemap_entradas(client)
    for camino in ("/terminos/", "/privacidad/"):
        url = next(u for u in entradas if u.endswith(camino))
        assert entradas[url]["changefreq"] == "yearly", camino
        assert entradas[url]["lastmod"] is None, camino


def test_el_lastmod_de_una_ciudad_sale_de_sus_propias_rutas(client, peru_airports):
    """Si saliera del máximo global, todas las ciudades mentirían la misma fecha."""
    from datetime import timedelta as _td
    lima = Route.objects.create(origin_id="LIM", destination_id="CUZ", is_monitored=True)
    pem = Route.objects.create(origin_id="PEM", destination_id="LIM", is_monitored=True)
    for r in (lima, pem):
        _snapshot(r, "179")
    RouteStats.objects.create(route=lima, avg_30d=Decimal("270"), min_30d=Decimal("150"),
                              p25_30d=Decimal("200"), median_30d=Decimal("260"), samples_count=40)
    vieja = RouteStats.objects.create(route=pem, avg_30d=Decimal("270"), min_30d=Decimal("150"),
                                      p25_30d=Decimal("200"), median_30d=Decimal("260"), samples_count=40)
    # `updated_at` es auto_now: se fuerza con update() para saltearlo.
    RouteStats.objects.filter(pk=vieja.pk).update(updated_at=timezone.now() - _td(days=9))

    # Los dos hubs necesitan un segundo destino para entrar al sitemap. Sus
    # estadísticas se dejan a la fecha de hoy: la del hub de Puerto Maldonado
    # tiene que seguir siendo la más reciente de SUS rutas, no la global.
    for origen, destino in (("LIM", "AQP"), ("PEM", "CUZ")):
        extra = Route.objects.create(origin_id=origen, destination_id=destino, is_monitored=True)
        _snapshot(extra, "179")
        RouteStats.objects.create(route=extra, avg_30d=Decimal("270"), min_30d=Decimal("150"),
                                  p25_30d=Decimal("200"), median_30d=Decimal("260"), samples_count=40)
    RouteStats.objects.filter(route__origin_id="PEM").update(
        updated_at=timezone.now() - _td(days=9))

    entradas = _sitemap_entradas(client)
    hub_lima = next(u for u in entradas if u.endswith("/vuelos/desde-lima/"))
    hub_pem = next(u for u in entradas if u.endswith("/vuelos/desde-puerto-maldonado/"))
    assert entradas[hub_lima]["lastmod"] != entradas[hub_pem]["lastmod"]


def test_los_titulos_no_se_truncan_en_los_resultados(client, peru_airports):
    """Google corta el title alrededor de los 60 caracteres.

    Se mide contra el peor caso REAL del catálogo, no contra un nombre corto:
    Puerto Maldonado es la ciudad de nombre más largo, y es la que hacía que el
    patrón de los hubs llegara a 83 caracteres.

    El peor par no es el obvio. `Lima a Puerto Maldonado` da 62, pero
    `Puerto Maldonado a Cusco` —que es una ruta real y monitoreada— da 63, y
    un test escrito sobre LIM-PEM lo dejaba pasar dando falsa seguridad. De ahí
    el tope: 63 es lo que cuesta la peor combinación dentro del patrón
    `Vuelos A a B`, y romperlo para ganar un carácter en 2 de 63 páginas no
    compensa. Si aparece una ciudad de nombre más largo, este test avisa.
    """
    import re
    rutas = [("LIM", "PEM"), ("PEM", "LIM"), ("CUZ", "PEM"), ("PEM", "CUZ")]
    for origen, destino in rutas:
        r = Route.objects.create(origin_id=origen, destination_id=destino, is_monitored=True)
        _snapshot(r, "179")

    for camino in ("/", "/cuando-comprar/", "/vuelos/desde-puerto-maldonado/",
                   "/vuelos/LIM-PEM/", "/vuelos/PEM-CUZ/", "/vuelos/CUZ-PEM/"):
        cuerpo = client.get(camino).content.decode()
        titulo = re.search(r"<title>(.*?)</title>", cuerpo, re.S).group(1).strip()
        assert len(titulo) <= 63, f"{camino}: {len(titulo)} caracteres — {titulo}"


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


def test_el_credito_del_pie_ya_no_pasa_por_configuracion(client, route, stats):
    """El distintivo se volvió portable: nombre, enlace y logo van en el marcado.

    Antes salía de `BUILDER_NAME` / `BUILDER_URL` / `BUILDER_LOGO`, y vaciar la
    primera lo ocultaba. Esas tres settings quedaron sin usar y se eliminaron,
    así que lo que hay que impedir es que alguien reintroduzca media dependencia:
    un `builder_*` en el contexto que la plantilla ya no lee sólo sirve para que
    el siguiente pierda una tarde descubriendo que no hace nada.

    Este test afirmaba `'class="foot-by"' not in cuerpo`. Esa clase desapareció
    con el rediseño, así que pasaba SIEMPRE: verificaba la ausencia de algo que
    ya no podía existir.
    """
    resp = client.get(reverse("web:home"))
    contexto = context_processors.site(resp.wsgi_request)
    assert not [k for k in contexto if k.startswith("builder_")], contexto

    cuerpo = resp.content.decode()
    assert "Star Insights IT by SISAC" in cuerpo
    assert 'href="https://sisac.pe/"' in cuerpo


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


def test_ningun_enlace_del_pie_esta_roto(client, route, stats):
    """Regresión: «Avisarme por correo» apuntaba a `/aviso/nuevo/` sin ruta y
    la vista contestaba 404. Un enlace muerto en TODAS las páginas del sitio, y
    justo en el formulario que captura correos.

    El test recorre el pie entero, no solo ese enlace: el error no fue de esa
    URL en particular, fue que nadie estaba mirando los enlaces del pie.

    Se siembra histórico porque el invariante que interesa es «en un sitio
    normal, ningún enlace del pie está roto». Sin datos, `/cuando-comprar/`
    contesta 404 a propósito —no publicamos un análisis sin muestras— y eso
    solo ocurre en una instalación recién creada, antes del primer barrido.
    """
    import re

    _historia_para_la_curva(route)
    cuerpo = client.get(reverse("web:home")).content.decode()
    pie = cuerpo.split("<footer")[1]
    internos = {
        u for u in re.findall(r'href="(/[^"#]*)"', pie)
        if not u.startswith("/static/")
    }
    assert internos, "el pie no tiene enlaces internos: el test no está mirando nada"

    for url in sorted(internos):
        assert client.get(url).status_code == 200, f"{url} está roto en el pie"


def test_avisarme_sin_ruta_ofrece_elegirla(client, route, stats):
    resp = client.get(reverse("web:nuevo_aviso"))
    assert resp.status_code == 200
    assert "¿Qué ruta quieres seguir?" in resp.content.decode()


def test_una_ruta_escrita_a_mano_que_no_existe_sigue_dando_404(client, route, stats):
    """Sin ruta se ofrece elegir; con una ruta concreta que no tenemos, 404.
    La persona pidió algo puntual y no lo tenemos."""
    assert client.get(reverse("web:nuevo_aviso"), {"ruta": "ZZZ-YYY"}).status_code == 404


def test_el_dataset_declara_su_licencia(client, route, stats):
    """Search Console lo pedía como aviso no crítico: sin `license`, Google
    sabe que hay un dataset pero no bajo qué condiciones se puede usar."""
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "creativecommons.org/licenses/by/4.0/" in cuerpo


def _historia_para_la_curva(route):
    """Suficientes muestras por semana de anticipación para que opine."""
    from datetime import timedelta as td

    hoy = timezone.localdate()
    filas = []
    for semana in range(5):
        dias = semana * 7 + 3
        precio = Decimal(400 - semana * 20)      # curva descendente clara
        for _ in range(60):
            filas.append(PriceSnapshot(
                route=route, flight_date=hoy + td(days=dias),
                min_price_pen=precio, avg_price_pen=precio,
                offers_count=3, days_ahead=dias,
            ))
    PriceSnapshot.objects.bulk_create(filas)


def test_cuando_comprar_publica_el_analisis(client, route, stats):
    _historia_para_la_curva(route)
    resp = client.get(reverse("web:cuando_comprar"))
    assert resp.status_code == 200

    cuerpo = resp.content.decode()
    assert "50 a 70 días" in cuerpo               # el mito que se desmiente
    assert "Cómo lo medimos" in cuerpo            # el método, que es el valor
    assert "FAQPage" in cuerpo                    # datos estructurados


def test_cuando_comprar_dice_hasta_donde_puede_hablar(client, route, stats):
    """Reconocer el límite del horizonte es lo que hace creíble el resto."""
    _historia_para_la_curva(route)
    cuerpo = client.get(reverse("web:cuando_comprar")).content.decode()
    assert "no lo medimos" in cuerpo


def test_sin_historico_la_pagina_no_existe(client, route, stats):
    """Publicarla vacía sería una promesa incumplida en el sitemap."""
    assert client.get(reverse("web:cuando_comprar")).status_code == 404


def test_cuando_comprar_esta_en_el_sitemap(client, route, stats):
    _historia_para_la_curva(route)
    assert "/cuando-comprar/" in client.get("/sitemap.xml").content.decode()


def test_la_ficha_muestra_la_foto_del_destino(client, route, stats):
    """La foto es del destino: es a donde va quien mira la página."""
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "ciudades/cusco" in cuerpo
    assert "ciudades/lima" not in cuerpo          # el origen no


def test_la_foto_reserva_su_espacio_y_carga_diferida(client, route, stats):
    """Sin `width`/`height` la página salta al cargar la imagen, que es lo que
    castiga Core Web Vitals; sin `lazy` compite con el dato por el ancho de
    banda en un móvil peruano."""
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    figura = cuerpo.split('class="foto')[1].split("</figure>")[0]
    assert 'width="1200"' in figura and 'height="400"' in figura
    assert 'loading="lazy"' in figura


def test_la_foto_lleva_texto_alternativo_y_credito(client, route, stats):
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert "Machu Picchu" in cuerpo               # alt descriptivo
    assert "Foto de" in cuerpo and "Unsplash" in cuerpo


def test_una_ciudad_sin_foto_no_dibuja_nada(client, peru_airports):
    """Solo hay foto verificada de cinco ciudades. Poner la plaza equivocada
    sería peor que no poner ninguna."""
    from apps.web import photos

    assert photos.FOTOS.get("puerto-maldonado") is None
    ruta = _ruta_publicada("LIM", "PEM")
    cuerpo = client.get(
        reverse("web:route", args=[ruta.origin_id, ruta.destination_id])
    ).content.decode()
    assert 'class="foto' not in cuerpo


def test_sin_correo_configurado_no_se_publica_ningun_contacto(client, route, stats, settings):
    """Publicar una dirección que rebota es peor que no publicar ninguna."""
    settings.CONTACT_EMAIL = ""
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "mailto:" not in cuerpo


def test_el_contacto_aparece_en_el_pie_y_en_las_legales(client, route, stats, settings):
    settings.CONTACT_EMAIL = "contacto@vueloradar.com"
    for url in (reverse("web:home"), reverse("web:terminos"), reverse("web:privacidad")):
        assert "mailto:contacto@vueloradar.com" in client.get(url).content.decode(), url


def test_sin_token_no_se_carga_analitica(client, route, stats, settings):
    """Cero peticiones a terceros mientras no haya analítica configurada."""
    settings.CLOUDFLARE_ANALYTICS_TOKEN = ""
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "cloudflareinsights" not in cuerpo


def test_con_token_la_analitica_se_carga_diferida(client, route, stats, settings):
    """`defer` para que no compita con el contenido por el ancho de banda."""
    settings.CLOUDFLARE_ANALYTICS_TOKEN = "0" * 32
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "static.cloudflareinsights.com/beacon.min.js" in cuerpo
    assert "<script defer" in cuerpo


def test_la_privacidad_solo_declara_analitica_si_la_hay(client, settings):
    """Mismo error que ya cometimos con AdSense: la página no puede afirmar
    que no hay analítica mientras el beacon carga."""
    settings.CLOUDFLARE_ANALYTICS_TOKEN = ""
    settings.ANALYTICS_ENABLED = False
    assert "Cloudflare Web Analytics" not in client.get(
        reverse("web:privacidad")).content.decode()

    settings.CLOUDFLARE_ANALYTICS_TOKEN = "0" * 32
    cuerpo = client.get(reverse("web:privacidad")).content.decode()
    assert "Cloudflare Web Analytics" in cuerpo
    assert "no tenemos instalada ninguna herramienta de analítica" not in cuerpo


def test_la_privacidad_declara_la_analitica_inyectada_en_el_borde(client, settings):
    """Cloudflare puede inyectar el beacon sin que nuestro HTML lo mencione.

    Verificado el 2026-08-28 con «automatic setup»: `curl` no ve el script y un
    navegador real sí. Atar la declaración al token nuestro haría que la página
    negara una analítica que está corriendo.
    """
    settings.CLOUDFLARE_ANALYTICS_TOKEN = ""     # no lo inyectamos nosotros
    settings.ANALYTICS_ENABLED = True            # pero está activa igual

    cuerpo = client.get(reverse("web:privacidad")).content.decode()
    assert "Cloudflare Web Analytics" in cuerpo
    # Y aun así no cargamos el script: duplicaría el conteo.
    assert "cloudflareinsights" not in cuerpo


def test_la_privacidad_declara_la_publicidad_cuando_esta_activa(client, settings):
    """La página no puede afirmar que no hay publicidad mientras el script carga.

    Regresión real: se activó AdSense y esta página siguió diciendo que la web
    no usaba cookies de terceros. Una política que contradice a la propia
    página no sirve de nada, y es motivo de rechazo en la revisión de AdSense.
    """
    settings.ADSENSE_CLIENT = "ca-pub-0000000000000000"
    cuerpo = client.get(reverse("web:privacidad")).content.decode()
    assert "AdSense" in cuerpo
    assert "myadcenter.google.com" in cuerpo


def test_sin_publicidad_la_privacidad_no_la_menciona(client, settings):
    settings.ADSENSE_CLIENT = ""
    cuerpo = client.get(reverse("web:privacidad")).content.decode()
    assert "AdSense" not in cuerpo


def test_el_pie_se_separa_del_contenido(client, route, stats):
    """Regresión: `.wrap` es una clase y `footer` un elemento, así que el
    `margin:0 auto;padding:0 1.5rem` de `.wrap` ganaba y dejaba el pie pegado a
    la última tarjeta. El selector tiene que llevar las dos cosas."""
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert "footer.wrap{" in cuerpo
    # Un `footer{` a secas volvería a perder contra `.wrap`.
    assert "\nfooter{" not in cuerpo


def test_el_logo_del_constructor_va_embebido_y_no_por_static(client, route, stats):
    """El logo era opcional por un riesgo concreto que ya no existe.

    Salía por `{% static %}`, y con `CompressedManifestStaticFilesStorage` un
    archivo ausente del manifiesto no degrada: revienta la página entera en
    producción. Por eso se podía apagar. Ahora va como data URI dentro del
    propio marcado, así que no hay estático que pueda faltar — pero eso vale
    sólo mientras siga embebido, que es lo que fija este test.
    """
    cuerpo = client.get(reverse("web:home")).content.decode()
    distintivo = cuerpo.split('class="sb-credito')[1]
    assert 'class="sb-marca"' in distintivo
    assert 'src="data:image/png;base64,' in distintivo
    assert "sisac-logo" not in distintivo, "el logo volvió a depender de un estático"


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


def test_un_anuncio_sin_relleno_no_deja_una_caja_vacia(client, route, stats, settings):
    """Google marca el <ins> con data-ad-status="unfilled" cuando no sirve nada.

    Sin la regla que colapsa el bloque queda un recuadro rotulado "Publicidad"
    con 6rem de vacío, y el rótulo lo vuelve más visible, no menos. Pasa
    mientras la cuenta no está aprobada, cuando nadie puja por el espacio y con
    cualquier bloqueador — o sea, la mayor parte del tiempo al principio.

    El selector tiene que alcanzar al `<aside>` entero: ocultar sólo el `<ins>`
    deja el marco y el rótulo dibujados sobre la nada.
    """
    settings.ADSENSE_CLIENT = "ca-pub-0000000000000000"
    settings.ADSENSE_SLOT_ROUTE = "1234567890"
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:route", args=["LIM", "CUZ"])).content.decode()
    assert '.ad:has(> .adsbygoogle[data-ad-status="unfilled"]){display:none}' in cuerpo


# --- páginas que explican el sitio -------------------------------------------
# AdSense rechazó el sitio por «contenido de bajo valor»: 40 fichas con el 79%
# del vocabulario en común y 18 hubs con el 95%. Estas dos son las únicas del
# sitio cuyo texto no puede salir de una plantilla, y por eso importan.

def test_como_medimos_responde_y_cita_datos_reales(client, route, stats):
    """Una metodología sin cifras propias la escribe cualquiera.

    Lo que la distingue de un folleto es que los números se puedan contrastar
    contra el propio sitio, así que salen del histórico en vivo y no del HTML.
    """
    _snapshot(route, "179")
    resp = client.get(reverse("web:como_medimos"))
    assert resp.status_code == 200
    cuerpo = resp.content.decode()
    assert "precios guardados" in cuerpo
    assert "IGV" in cuerpo and "TUUA" in cuerpo


def test_como_medimos_publica_lo_que_no_sabe(client, route, stats):
    """La sección de límites es la que separa metodología de publicidad.

    Si desaparece, la página pasa a contar solo los aciertos y deja al lector
    sin forma de calibrar cuánto confiar en el veredicto.
    """
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:como_medimos")).content.decode()
    assert "Lo que todavía no sabemos" in cuerpo
    assert "equipaje" in cuerpo.lower()


def test_acerca_dice_que_no_se_cobra_comision(client, route, stats):
    """Es la promesa que sostiene la marca: sin ella el veredicto no vale nada.

    Un sitio que cobrara por venta nunca diría «espera». Que esté escrito y
    testeado evita que se caiga en un rediseño.
    """
    _snapshot(route, "179")
    resp = client.get(reverse("web:acerca"))
    assert resp.status_code == 200
    cuerpo = resp.content.decode()
    assert "no cobramos comisión" in cuerpo.lower()
    assert "Star Insights IT by SISAC" in cuerpo


def test_las_dos_paginas_se_alcanzan_desde_el_pie(client, route, stats):
    """Una página sin enlace interno es una página que Google indexa flojo.

    Ya pasó con el Libro de Reclamaciones: existía y no había forma de llegar.
    """
    _snapshot(route, "179")
    cuerpo = client.get(reverse("web:home")).content.decode()
    assert reverse("web:como_medimos") in cuerpo
    assert reverse("web:acerca") in cuerpo


def test_las_dos_paginas_estan_en_el_sitemap(client, route, stats):
    _snapshot(route, "179")
    entradas = _sitemap_entradas(client)
    for nombre in ("web:como_medimos", "web:acerca"):
        camino = reverse(nombre)
        assert any(u.endswith(camino) for u in entradas), f"{camino} no está en el sitemap"


def test_el_analisis_de_la_ciudad_va_antes_del_resto_de_destinos(client, peru_airports):
    """Con 17 destinos el análisis quedaba a 2.178 px: cinco pantallas de móvil.

    Es lo único que el hub tiene y ninguna otra página del sitio: enterrarlo
    bajo la lista completa era publicarlo para nadie. La lista se parte, y este
    test fija el orden — no que exista, que es lo que ya se cumplía antes.
    """
    from apps.flights.models import Airport

    # `peru_airports` trae cuatro; hacen falta más para pasar del corte.
    for iata, ciudad in (("IQT", "Iquitos"), ("TPP", "Tarapoto"),
                         ("PIU", "Piura"), ("TRU", "Trujillo")):
        Airport.objects.create(iata_code=iata, name=ciudad, city=ciudad, region=ciudad)

    for destino in ("CUZ", "AQP", "PEM"):
        _ruta_publicada("LIM", destino)
    cuerpo = client.get(reverse("web:hub", args=["lima"])).content.decode()
    assert "El resto de destinos" not in cuerpo, "con 3 destinos no hay que partir nada"

    for destino in ("IQT", "TPP", "PIU", "TRU"):
        _ruta_publicada("LIM", destino)
    cuerpo = client.get(reverse("web:hub", args=["lima"])).content.decode()
    assert "El resto de destinos" in cuerpo, "con 7 destinos la lista se parte"
    assert cuerpo.index("El resto de destinos") > cuerpo.index('class="routes"'), (
        "el resto tiene que ir DESPUÉS de los primeros destinos"
    )


def test_la_escala_de_precio_se_calcula_sobre_el_rango(client, peru_airports):
    """Sobre el precio, entre S/ 146 y S/ 260 las barras irían del 56% al 100%.

    Es la misma lección que las barras de `insights`: comprimidas contra el
    tope no dejan comparar nada. Y el mínimo lleva piso, porque una barra de
    ancho cero se lee como un fallo de render, no como «este es el más barato».
    """
    from apps.web.views import PISO_ESCALA, _marcar_escala

    destinos = [{"desde": Decimal(p)} for p in ("146", "203", "260")]
    _marcar_escala(destinos, destinos)
    assert destinos[0]["ancho_pct"] == PISO_ESCALA
    assert destinos[2]["ancho_pct"] == 100
    assert destinos[1]["ancho_pct"] == pytest.approx((PISO_ESCALA + 100) / 2, abs=1)
    assert destinos[0]["es_mas_barato"] and not destinos[2]["es_mas_barato"]


def test_una_sola_ciudad_con_un_precio_no_rompe_la_escala(client, peru_airports):
    """Rango cero: sin la guarda, la división reventaría la página entera."""
    from apps.web.views import PISO_ESCALA, _marcar_escala

    destinos = [{"desde": Decimal("200")}, {"desde": Decimal("200")}]
    _marcar_escala(destinos, destinos)
    assert all(d["ancho_pct"] == PISO_ESCALA for d in destinos)


def test_el_hub_cuenta_todos_sus_destinos_aunque_parta_la_lista(client, peru_airports):
    """Partir la lista dejó `destinos|length` valiendo 6 en una ciudad de 17.

    No era solo el texto: la meta description y el Open Graph anunciaban «6
    destinos», que es lo que Google y WhatsApp leen. Lo cazó una inspección en
    el navegador, no la suite — de ahí este test.
    """
    from apps.flights.models import Airport

    for iata, ciudad in (("IQT", "Iquitos"), ("TPP", "Tarapoto"),
                         ("PIU", "Piura"), ("TRU", "Trujillo")):
        Airport.objects.create(iata_code=iata, name=ciudad, city=ciudad, region=ciudad)
    for destino in ("CUZ", "AQP", "PEM", "IQT", "TPP", "PIU", "TRU"):
        _ruta_publicada("LIM", destino)

    cuerpo = client.get(reverse("web:hub", args=["lima"])).content.decode()
    assert "7 destinos" in cuerpo
    assert "6 destinos" not in cuerpo
