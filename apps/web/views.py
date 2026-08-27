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

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import cache_control

from apps.flights.models import Route

from . import chart, queries
from .verdict import evaluate, evaluate_trend

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

        rutas.append({
            "route": route,
            "desde": desde,
            "fechas": len(fechas),
            "baratas": len(baratas),
            # `desde` es el mínimo vigente sobre la misma ventana que el calendario.
            "trend": evaluate_trend(
                desde, [d.price for d in historia_por_ruta.get(route.pk, [])]
            ),
        })

    # Primero donde hay más oportunidad real de comprar barato.
    rutas.sort(key=lambda r: (-r["baratas"], r["route"].priority))

    return render(request, "web/home.html", {
        "ciudades": queries.cities_with_routes(),
        "rutas": rutas,
        "total_rutas": len(rutas),
        "con_oportunidad": [r for r in rutas if r["baratas"]],
    })


@cache_control(public=True, max_age=300, s_maxage=EDGE_TTL)
def route_detail(request, origin: str, destination: str):
    """Ficha de una ruta: precio desde, veredicto por fecha, histórico y calendario."""
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
        "related": queries.related_routes(route),
        "chart": chart.build(historia),
        "history_days": len(historia),
        "all_time_low": queries.all_time_low(route),
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
        "updated_at": timezone.now(),
    })


@cache_control(public=True, max_age=3600, s_maxage=86400)
def legal(request, pagina: str):
    """Términos y privacidad. Cambian poco: se cachean un día en el borde."""
    if pagina not in ("terminos", "privacidad"):
        raise Http404("Página legal desconocida")
    return render(request, f"web/{pagina}.html", {"updated_at": timezone.now()})


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
