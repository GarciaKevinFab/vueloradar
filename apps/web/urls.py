"""Rutas públicas. Las URLs llevan IATA para ser estables y legibles:
/vuelos/LIM-CUZ/ nunca cambia aunque cambie el nombre de la ciudad.
"""

from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("vuelos/<str:origin>-<str:destination>/", views.route_detail, name="route"),
]
