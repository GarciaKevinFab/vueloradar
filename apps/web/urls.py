"""Rutas públicas. Las URLs llevan IATA para ser estables y legibles:
/vuelos/LIM-CUZ/ nunca cambia aunque cambie el nombre de la ciudad.
"""

from django.urls import path, re_path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("buscar/", views.buscar, name="buscar"),
    # Antes que `vuelos/...`: es una página propia, no una ruta de vuelo.
    path("cuando-comprar/", views.cuando_comprar, name="cuando_comprar"),
    path("como-medimos/", views.como_medimos, name="como_medimos"),
    path("acerca/", views.acerca, name="acerca"),
    path("aviso/nuevo/", views.nuevo_aviso, name="nuevo_aviso"),
    path("aviso/confirmar/<str:token>/", views.confirmar_aviso, name="confirmar_aviso"),
    path("aviso/baja/<str:token>/", views.baja_aviso, name="baja_aviso"),
    path("terminos/", views.legal, {"pagina": "terminos"}, name="terminos"),
    path("privacidad/", views.legal, {"pagina": "privacidad"}, name="privacidad"),
    path("reclamaciones/", views.legal, {"pagina": "reclamaciones"}, name="reclamaciones"),
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
