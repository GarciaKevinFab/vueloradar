"""Páginas públicas: portada y ficha por ruta.

La ficha de ruta es el activo SEO del proyecto. Responde la pregunta que ni
Google Flights ni los metabuscadores responden en una página indexable:
*¿el precio de esta fecha es bueno para esta ruta?*

Dos veredictos distintos, porque comparan cosas distintas:

- **por fecha**: el precio de un día contra la distribución de la ruta. Válido
  desde el primer mes de histórico y es lo que llena el calendario.
- **de tendencia**: el mínimo de hoy contra el histórico de mínimos diarios.
  Necesita dos semanas de serie; hasta entonces decimos que no sabemos.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import cache_control, never_cache

from apps.flights.models import Route

from . import calendar_grid, chart, insights, og_images, photos, queries, search
from .verdict import MIN_TREND_DAYS, evaluate, evaluate_trend

#: El barrido corre 06:00 y 18:00; media hora de caché en el borde es seguro
#: y descarga por completo al VPS. Cloudflare respeta `s-maxage`.
EDGE_TTL = 60 * 30


def _fechas_con_veredicto(upcoming, stats):
    """Anota cada fecha del calendario con su veredicto propio."""
    return [{"day": d.day, "price": d.price, "verdict": evaluate(d.price, stats)} for d in upcoming]


@cache_control(public=True, max_age=300, s_maxage=EDGE_TTL)
def home(request):
    """Portada: cada ruta con su precio más bajo y cuántas fechas están baratas.

    Todo el histórico de todas las rutas se trae en dos consultas agregadas.
    Hacerlo ruta por ruta costaba 121 consultas y 22 s, y ese costo lo paga
    entero el primer visitante después de cada purga del borde.
    """
    routes = list(queries.published_routes())
    ids = [r.pk for r in routes]
    upcoming_por_ruta = queries.bulk_upcoming_prices(ids)
    historia_por_ruta = queries.bulk_price_history(ids)

    rutas = []
    for route in routes:
        stats = getattr(route, "stats", None)
        fechas = _fechas_con_veredicto(upcoming_por_ruta.get(route.pk, []), stats)
        baratas = [f for f in fechas if f["verdict"].should_buy]
        desde = min((f["price"] for f in fechas), default=None)

        serie = [d.price for d in historia_por_ruta.get(route.pk, [])]
        rutas.append({
            "route": route,
            "desde": desde,
            "fechas": len(fechas),
            "baratas": len(baratas),
            "spark": chart.sparkline(serie),
            # `desde` es el mínimo vigente sobre la misma ventana que el calendario.
            "trend": evaluate_trend(
                desde, [d.price for d in historia_por_ruta.get(route.pk, [])]
            ),
        })

    # Primero donde hay más oportunidad real de comprar barato.
    rutas.sort(key=lambda r: (-r["baratas"], r["route"].priority))

    # El pulso del dia: lo que hace que la portada muestre el dato y no una
    # lista de texto. Todo sale de lo que ya se calculo arriba.
    con_precio = [r for r in rutas if r["desde"] is not None]
    mas_barata = min(con_precio, key=lambda r: r["desde"]) if con_precio else None

    return render(request, "web/home.html", {
        "ciudades": queries.cities_with_routes(),
        "rutas": rutas,
        "total_rutas": len(rutas),
        "total_baratas": sum(r["baratas"] for r in rutas),
        "total_fechas": sum(r["fechas"] for r in rutas),
        "mas_barata": mas_barata,
        "snapshots": queries.total_snapshots(),
        # Lo que solo se puede decir habiendo medido. Tres consultas agregadas
        # de costo fijo: no crecen con las rutas, que es el invariante que
        # protege el presupuesto de la portada.
        # Prefijo `insight_` porque `dias_semana` ya existe en la ficha de ruta
        # con otro significado (las cabeceras del calendario).
        "insight_dia": insights.weekday_prices(),
        "insight_ventana": insights.booking_windows(),
        "insight_aerolineas": insights.cheapest_airlines(),
    })


@cache_control(public=True, max_age=300, s_maxage=EDGE_TTL)
def route_detail(request, origin: str, destination: str):
    """Ficha de una ruta: precio desde, veredicto por fecha, histórico y calendario."""
    # La URL acepta cualquier caja a propósito, para que un enlace tecleado a
    # mano o copiado en minúsculas llegue igual. Pero sólo una forma es
    # canónica, y hay que redirigir a ella: el `<link rel="canonical">` se arma
    # con la URL pedida, así que sin este 301 la misma ficha vive en 64 URLs
    # (2^6 combinaciones de mayúsculas) y cada una se declara canónica de sí
    # misma. Google las trata como duplicados sin canónica elegida.
    #
    # La canónica es MAYÚSCULAS porque es lo que ya emiten los `{% url %}`, el
    # sitemap y los nombres de las imágenes OG. Pasar a minúsculas obligaría a
    # tocar diez plantillas y renombrar 63 archivos sin ganar nada.
    if (origin, destination) != (origin.upper(), destination.upper()):
        return redirect(
            "web:route", origin.upper(), destination.upper(), permanent=True
        )
    origin, destination = origin.upper(), destination.upper()
    try:
        # Mismo criterio que el sitemap: una ruta sin histórico no tiene nada
        # que mostrar y publicarla sería una página vacía indexable.
        # CHM y RIM entran acá: no tienen servicio comercial regular.
        route = queries.published_routes().get(origin_id=origin, destination_id=destination)
    except Route.DoesNotExist as exc:
        raise Http404("Ruta sin histórico publicado") from exc

    stats = queries.stats_for(route)
    upcoming = queries.upcoming_prices(route)
    fechas = _fechas_con_veredicto(upcoming, stats)
    historia = queries.price_history(route)

    return render(request, "web/route.html", {
        "route": route,
        "stats": stats,
        "desde": min((f["price"] for f in fechas), default=None),
        "fechas": fechas,
        "baratas": [f for f in fechas if f["verdict"].should_buy],
        "caras": [f for f in fechas if f["verdict"].level in ("alto", "caro")],
        "cheapest": sorted(fechas, key=lambda f: f["price"])[:5],
        "trend": evaluate_trend(
            queries.current_min_price(route), [d.price for d in historia]
        ),
        "semanas": calendar_grid.build(fechas),
        "dias_semana": calendar_grid.DIAS,
        "related": queries.related_routes(route),
        "chart": chart.build(historia, stats),
        "history_days": len(historia),
        "all_time_low": queries.all_time_low(route),
        # Los mismos análisis, pero de esta ruta: el mejor día para volar a
        # Cusco no tiene por qué ser el mejor para volar a Iquitos.
        # La foto es del DESTINO: es a donde va quien mira esta página.
        "foto": photos.foto_de(route.destination),
        "og_image": _og_de_ruta(route),
        # El texto que se manda por WhatsApp. Se arma acá y no en la plantilla
        # porque lleva el precio, y un `{% if %}` anidado dentro de un atributo
        # `href` es donde se cuelan los errores de comillas.
        "comparte_texto": _texto_para_compartir(route, fechas),
        "insight_dia": insights.weekday_prices(route),
        "insight_ventana": insights.booking_windows(route),
        "insight_aerolineas": insights.cheapest_airlines(route),
        "updated_at": timezone.now(),
    })


@cache_control(public=True, max_age=300, s_maxage=EDGE_TTL)
def city_hub(request, ciudad: str):
    """Todas las rutas que salen de una ciudad.

    Captura la intención "vuelos baratos desde Lima", que la ficha de una ruta
    concreta no puede responder, y de paso reparte enlaces hacia las fichas:
    sin esto cada ficha depende solo de la portada.
    """
    airport = queries.airport_by_slug(ciudad)
    if airport is None:
        raise Http404("Ciudad sin rutas publicadas")

    routes = queries.routes_from(airport)
    ids = [r.pk for r in routes]
    upcoming_por_ruta = queries.bulk_upcoming_prices(ids)

    destinos = []
    for route in routes:
        fechas = _fechas_con_veredicto(
            upcoming_por_ruta.get(route.pk, []), getattr(route, "stats", None)
        )
        destinos.append({
            "route": route,
            "desde": min((f["price"] for f in fechas), default=None),
            "fechas": len(fechas),
            "baratas": len([f for f in fechas if f["verdict"].should_buy]),
        })

    con_precio = [d for d in destinos if d["desde"] is not None]
    destinos.sort(key=lambda d: (d["desde"] is None, d["desde"] or 0))

    return render(request, "web/hub.html", {
        "airport": airport,
        "destinos": destinos,
        "mas_barato": min((d["desde"] for d in con_precio), default=None),
        "otras_ciudades": [c for c in queries.cities_with_routes() if c.pk != airport.pk],
        "foto": photos.foto_de(airport),
        "og_image": (f"web/og/desde-{airport.slug}.png"
                     if airport.slug in og_images.CIUDADES else None),
        # Análisis de la ciudad, no del país: desde Cusco el mejor día para
        # volar no tiene por qué ser el mismo que desde Lima, y es justo ese
        # contraste el que hace que el hub diga algo que la portada no dice.
        # Los mínimos de muestras de `insights` deciden si hay algo que decir;
        # si no lo hay, el bloque no se dibuja.
        "insight_dia": insights.weekday_prices(origen=airport.iata_code),
        "insight_ventana": insights.booking_windows(origen=airport.iata_code),
        "insight_aerolineas": insights.cheapest_airlines(origen=airport.iata_code),
        "indexable": queries.hub_indexable(routes),
        "updated_at": timezone.now(),
    })


#: Tope de pasajeros. Mas alla de esto Google Flights cotiza distinto y la
#: referencia por adulto que mostramos deja de tener sentido.
MAX_ADULTOS = 9
MAX_NINOS = 8


def _entero(valor, minimo: int, maximo: int, defecto: int) -> int:
    """Entero acotado desde la query string. Cualquier basura cae al defecto."""
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return defecto


def buscar(request):
    """Buscador por texto libre sobre el histórico.

    No pasa por ningún modelo de IA ni scrapea en vivo: ver el docstring de
    `apps/web/search.py` para el porqué. Responde con lo que ya observamos y
    con el link de compra armado para los pasajeros pedidos.
    """
    from apps.flights.models import Airport, Route

    texto = (request.GET.get("q") or "").strip()[:120]
    adultos = _entero(request.GET.get("adultos"), 1, MAX_ADULTOS, 1)
    ninos = _entero(request.GET.get("ninos"), 0, MAX_NINOS, 0)

    contexto = {
        "texto": texto,
        "adultos": adultos,
        "ninos": ninos,
        "rango_adultos": range(1, MAX_ADULTOS + 1),
        "rango_ninos": range(0, MAX_NINOS + 1),
        "ejemplos": [
            "de Lima a Cusco el 15 de setiembre",
            "Arequipa a Lima mañana",
            "Huancayo a Lima 2026-10-14",
        ],
    }
    if not texto:
        return render(request, "web/buscar.html", contexto)

    consulta = search.parse_consulta(texto, Airport.objects.all())
    contexto["consulta"] = consulta

    if not consulta.es_completa:
        contexto["falta"] = _que_falta(consulta)
        return render(request, "web/buscar.html", contexto)

    contexto["enlace_compra"] = search.booking_url(
        consulta.origen, consulta.destino, consulta.fecha, adultos, ninos
    )

    route = (
        Route.objects.select_related("origin", "destination")
        .filter(origin_id=consulta.origen, destination_id=consulta.destino)
        .first()
    )
    contexto["route"] = route
    if route is None:
        return render(request, "web/buscar.html", contexto)

    contexto["publicada"] = queries.published_routes().filter(pk=route.pk).exists()
    dia = next(
        (d for d in queries.upcoming_prices(route) if d.day == consulta.fecha), None
    )
    if dia is not None:
        contexto["precio"] = dia.price
        contexto["veredicto"] = evaluate(dia.price, queries.stats_for(route))

    return render(request, "web/buscar.html", contexto)


def _que_falta(consulta) -> str:
    """Qué no se entendió, dicho de una forma que se pueda corregir."""
    if not consulta.origen and not consulta.destino:
        return "No reconocimos ninguna ciudad."
    if not consulta.destino:
        return "Falta el destino: escribe las dos ciudades."
    if not consulta.fecha:
        return "Falta la fecha. Puedes escribirla como «15 de setiembre», «15/09» o «mañana»."
    return "No pudimos entender la consulta."


# --- Avisos por correo ------------------------------------------------------
# El formulario NO puede vivir en la ficha de ruta: esa pagina se cachea en el
# borde, y un token CSRF cacheado seria el mismo para todos los visitantes, asi
# que todos los envios fallarian. Por eso la ficha enlaza y el formulario vive
# en su propia pagina sin caché.


def _ip(request) -> str:
    """IP real detrás de Cloudflare."""
    return request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "")


def _supera_el_limite(request) -> bool:
    """Un formulario público que manda correo es un vector de spam."""
    from django.core.cache import cache

    clave = f"aviso:ip:{_ip(request)}"
    try:
        cache.add(clave, 0, 3600)
        return cache.incr(clave) > settings.EMAIL_ALERTS_PER_IP_PER_HOUR
    except Exception:  # noqa: BLE001 - sin cache no se bloquea a nadie
        return False


@never_cache
def nuevo_aviso(request):
    """Alta de un aviso por correo, con doble opt-in."""
    from apps.alerts.mailer import nuevo_token, send_confirmation_email
    from apps.alerts.models import Alert
    from apps.flights.models import Route

    codigo = (request.GET.get("ruta") or request.POST.get("ruta") or "").upper()

    # Sin ruta se ofrece elegirla, no un 404. El pie de TODAS las páginas
    # enlazaba acá sin parámetro, así que "Avisarme por correo" llevaba a
    # "esta página no existe" en todo el sitio.
    if not codigo:
        return render(request, "web/aviso_elegir.html", {
            "rutas": queries.published_routes(),
            "updated_at": timezone.now(),
        })

    origen, _, destino = codigo.partition("-")
    route = (
        Route.objects.select_related("origin", "destination")
        .filter(origin_id=origen, destination_id=destino)
        .first()
    )
    # Con una ruta escrita a mano que no existe sí es 404: la persona pidió
    # algo concreto que no tenemos.
    if route is None:
        raise Http404("Ruta desconocida")

    fecha_texto = request.GET.get("fecha") or request.POST.get("fecha") or ""
    fecha = parse_date(fecha_texto) if fecha_texto else None

    contexto = {"route": route, "fecha": fecha, "bot_deeplink": f"{route.origin_id}-{route.destination_id}"}

    if request.method != "POST":
        return render(request, "web/aviso.html", contexto)

    correo = (request.POST.get("email") or "").strip()[:254]
    try:
        validate_email(correo)
    except ValidationError:
        contexto["error"] = "Ese correo no parece válido. Revísalo y prueba de nuevo."
        contexto["email"] = correo
        return render(request, "web/aviso.html", contexto)

    if _supera_el_limite(request):
        contexto["error"] = "Demasiados avisos desde esta conexión. Prueba de nuevo en un rato."
        return render(request, "web/aviso.html", contexto)

    alerta, creada = Alert.objects.get_or_create(
        email=correo, route=route, flight_date=fecha,
        alert_type=Alert.TYPE_DEAL_DETECTED,
        defaults={"token": nuevo_token()},
    )
    if not alerta.token:
        alerta.token = nuevo_token()
        alerta.save(update_fields=["token"])
    if not alerta.is_active:
        Alert.objects.filter(pk=alerta.pk).update(is_active=True)

    ya_confirmado = alerta.email_confirmed_at is not None
    if not ya_confirmado:
        send_confirmation_email(alerta, settings.SITE_BASE_URL)

    contexto["enviado"] = True
    contexto["ya_confirmado"] = ya_confirmado
    contexto["email"] = correo
    return render(request, "web/aviso.html", contexto)


@never_cache
def confirmar_aviso(request, token: str):
    """El enlace del correo. Recién acá la alerta empieza a notificar."""
    from apps.alerts.models import Alert

    alerta = Alert.objects.select_related("route__origin", "route__destination").filter(
        token=token
    ).first()
    if alerta is None:
        raise Http404("Enlace vencido o inexistente")

    if alerta.email_confirmed_at is None:
        Alert.objects.filter(pk=alerta.pk).update(
            email_confirmed_at=timezone.now(), is_active=True
        )
        alerta.refresh_from_db()

    return render(request, "web/aviso_confirmado.html", {"alerta": alerta})


@never_cache
def baja_aviso(request, token: str):
    """Baja en un clic, sin pedir nada. Todo correo lleva este enlace."""
    from apps.alerts.models import Alert

    alerta = Alert.objects.select_related("route__origin", "route__destination").filter(
        token=token
    ).first()
    if alerta is None:
        raise Http404("Enlace vencido o inexistente")

    Alert.objects.filter(pk=alerta.pk).update(is_active=False)
    return render(request, "web/aviso_baja.html", {"alerta": alerta})


@cache_control(public=True, max_age=3600, s_maxage=86400)
def legal(request, pagina: str):
    """Términos y privacidad. Cambian poco: se cachean un día en el borde."""
    if pagina not in ("terminos", "privacidad", "reclamaciones"):
        raise Http404("Página legal desconocida")
    return render(request, f"web/{pagina}.html", {"updated_at": timezone.now()})


@cache_control(public=True, max_age=300, s_maxage=EDGE_TTL)
def como_medimos(request):
    """Cómo se obtiene cada precio, qué incluye y qué NO sabemos.

    Es la página que un sitio de precios tiene que poder escribir y casi
    ninguno escribe. No es relleno para SEO: el resto del sitio afirma cosas
    —«está barato», «compra con tres semanas»— y sin explicar de dónde salen,
    esas afirmaciones no se distinguen de las de cualquier comparador.

    Cita números en vivo a propósito. «Medimos muchas rutas a diario» lo
    escribe cualquiera; «llevamos N precios guardados desde tal fecha» se puede
    contrastar contra el propio sitio, y es lo que la convierte en contenido.

    Los límites conocidos van dentro, no en letra chica. Decir qué no sabemos
    es lo que separa una metodología de un folleto, y es justo lo que un
    visitante necesita para saber cuánto confiar en el veredicto.
    """
    return render(request, "web/como_medimos.html", {
        "cobertura": queries.cobertura(),
        "horizonte": insights.SEMANAS[-1][1],
        "min_muestras": settings.VERDICT_MIN_SAMPLES,
        "dias_tendencia": MIN_TREND_DAYS,
        "updated_at": timezone.now(),
    })


@cache_control(public=True, max_age=300, s_maxage=EDGE_TTL)
def acerca(request):
    """Quién está detrás, para qué existe el sitio y cómo se sostiene.

    Un sitio que opina sobre precios sin decir quién lo hace ni de qué vive
    obliga al visitante a suponerlo, y lo que se supone es lo peor: que cobra
    comisión de alguna aerolínea. Decirlo explícitamente es más barato que
    parecerlo.
    """
    return render(request, "web/acerca.html", {
        "cobertura": queries.cobertura(),
        "updated_at": timezone.now(),
    })


@cache_control(public=True, max_age=300, s_maxage=EDGE_TTL)
def cuando_comprar(request):
    """La respuesta a «¿cuándo conviene comprar un vuelo en Perú?».

    Existe porque es la única pregunta que un comparador no puede responder sin
    haber estado midiendo, y porque la respuesta que repite la prensa peruana
    —comprar con 50 a 70 días— contradice lo que muestra el histórico.

    Se publica solo si hay datos para sostenerla: sin curva, la página no
    tendría nada que decir y sería una promesa vacía en el sitemap.
    """
    curva = insights.booking_curve()
    if curva is None:
        raise Http404("Todavía no hay histórico suficiente para publicar esto")

    return render(request, "web/cuando_comprar.html", {
        "curva": curva,
        "insight_ventana": insights.booking_windows(),
        "insight_dia": insights.weekday_prices(),
        "insight_aerolineas": insights.cheapest_airlines(),
        "snapshots": queries.total_snapshots(),
        "dias_de_historia": insights.DIAS_DE_HISTORIA,
        # El horizonte del barrido: hasta dónde podemos hablar con datos.
        "horizonte": insights.SEMANAS[-1][1],
        "updated_at": timezone.now(),
    })


def _og_de_ruta(route) -> str | None:
    """Imagen de compartir de una ruta, si se generó.

    Se pregunta contra un `frozenset` en memoria y no contra el disco: con el
    manifiesto de estáticos, referenciar un archivo que no está tira 500 en
    producción, así que preguntar antes es la diferencia entre una página y un
    error. Las genera `scripts/build_og_images.py`.
    """
    clave = f"{route.origin_id}-{route.destination_id}"
    return f"web/og/{clave}.png" if clave in og_images.RUTAS else None


def _texto_para_compartir(route, fechas) -> str:
    """Lo que le llega a quien recibe el enlace por WhatsApp.

    Lleva el precio porque es el gancho, pero el mensaje lo escribe la persona
    en el momento: si el precio cambió, quien hace clic ve el actual en la
    página. Distinto de la imagen de compartir, que se genera una vez y por eso
    NO lleva precio.
    """
    ruta = f"{route.origin.city} a {route.destination.city}"
    precios = [f["price"] for f in fechas]
    if not precios:
        return f"Mira el histórico de precios de vuelos {ruta} en VueloRadar:"

    baratas = len([f for f in fechas if f["verdict"].should_buy])
    desde = min(precios)
    if baratas:
        return (f"Vuelos {ruta} desde S/ {desde:.0f}. "
                f"Hay {baratas} fecha{'s' if baratas != 1 else ''} a buen precio "
                f"según el histórico:")
    return f"Vuelos {ruta} desde S/ {desde:.0f}, según el histórico real:"


def ads_txt(request):
    """`/ads.txt`: declara quién puede vender el inventario de este dominio.

    AdSense no paga sin este archivo: sin él el inventario queda como no
    autorizado y los anunciantes no pujan. El ID sale de `ADSENSE_CLIENT` para
    no mantener el mismo número en dos lugares; sin ID configurado devolvemos
    404 en vez de un archivo con un `pub-` vacío, que sería una declaración
    falsa sobre quién puede vender esta publicidad.
    """
    cliente = getattr(settings, "ADSENSE_CLIENT", "")
    if not cliente:
        raise Http404("Sin publicidad configurada")
    # El archivo lleva el ID sin el prefijo `ca-`, que es solo para el script.
    editor = cliente.removeprefix("ca-")
    return HttpResponse(
        f"google.com, {editor}, DIRECT, f08c47fec0942fa0\n",
        content_type="text/plain",
    )


def robots_txt(request):
    """Indexable, con el sitemap declarado y el admin fuera del índice."""
    host = request.get_host()
    scheme = "https" if request.is_secure() else "http"
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /healthz",
        "",
        f"Sitemap: {scheme}://{host}/sitemap.xml",
        "",
    ])
    return HttpResponse(body, content_type="text/plain")
