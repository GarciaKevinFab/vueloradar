"""Rutas públicas. Las URLs llevan IATA para ser estables y legibles:
/vuelos/LIM-CUZ/ nunca cambia aunque cambie el nombre de la ciudad.
"""

from django.urls import path, re_path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("buscar/", views.buscar, name="buscar"),
    path("terminos/", views.legal, {"pagina": "terminos"}, name="terminos"),
    path("privacidad/", views.legal, {"pagina": "privacidad"}, name="privacidad"),
    # El hub va primero y la ficha exige tres letras por lado: sin esa
    # restricción, /vuelos/desde-lima/ entraría como origen "desde" y
    # destino "lima".
    path("vuelos/desde-<slug:ciudad>/", views.city_hub, name="hub"),
    re_path(
        r"^vuelos/(?P<origin>[A-Za-z]{3})-(?P<destination>[A-Za-z]{3})/$",
        views.route_detail,
        name="route",
    ),
]
