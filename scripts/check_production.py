"""Verificación previa al despliegue, con los ajustes reales de producción.

    DJANGO_DEBUG=False DJANGO_ALLOWED_HOSTS=vueloradar.com python scripts/check_production.py

La suite de tests corre con `DEBUG=False` (Django lo fuerza), pero usa el
storage de estáticos plano y `config.settings_test`. Lo que ningún test toca es
la combinación que sí se despliega: `config.settings` con
`CompressedManifestStaticFilesStorage`. Ahí es donde un `{% static %}` sin
entrada en el manifiesto tumba la página entera, y solo aparece en producción.

Complementa `manage.py check --deploy`, que valida cabeceras de seguridad pero
no renderiza ninguna página ni resuelve un solo estático.

Sale con código distinto de cero si algo falla, para encadenarlo en el
despliegue. Necesita la base de datos: se corre en el VPS, no en CI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_DEBUG", "False")
# Sin ALLOWED_HOSTS definido (correr esto fuera del servidor) hace falta uno.
# Con el .env cargado gana el del entorno, que es justamente lo que se verifica.
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost")

import django  # noqa: E402

django.setup()

# Django loguea cada 400/404 como error. Acá se provocan a propósito y ese
# ruido tapa por completo el resultado del chequeo.
import logging  # noqa: E402

logging.disable(logging.WARNING)

from django.conf import settings  # noqa: E402
from django.templatetags.static import static  # noqa: E402
from django.test import Client  # noqa: E402

#: Todo lo que las plantillas referencian por `{% static %}`.
ASSETS = [
    "web/favicon.ico",
    "web/favicon-32.png",
    "web/apple-touch-icon.png",
    "web/og.png",
]

fallos: list[str] = []

#: El host sale de ALLOWED_HOSTS y no está fijo: en el servidor la variable
#: viene del .env, y un "testserver" hardcodeado devuelve 400 en todo.
HOST = next((h for h in settings.ALLOWED_HOSTS if h != "*"), "testserver")


def check(descripcion: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  ok    {descripcion}")
        return
    print(f"  FALLA {descripcion}{(' — ' + detalle) if detalle else ''}")
    fallos.append(descripcion)


def _resultado() -> int:
    print()
    if fallos:
        print(f"{len(fallos)} verificación(es) fallida(s). No desplegar.")
        return 1
    print("Todo en orden para desplegar.")
    return 0


def main() -> int:
    print(f"Ajustes (host: {HOST})")
    check("DEBUG está apagado", settings.DEBUG is False, f"DEBUG={settings.DEBUG}")
    backend = settings.STORAGES["staticfiles"]["BACKEND"]
    check("storage de estáticos con manifiesto", "Manifest" in backend, backend)

    print("Estáticos (resolución vía manifiesto)")
    for asset in ASSETS:
        try:
            url = static(asset)
            check(f"{asset} resuelve", bool(url), url)
        except ValueError as exc:
            check(f"{asset} resuelve", False, str(exc))

    print("Páginas")
    client = Client()
    from apps.web import queries

    ruta = queries.published_routes().first()
    if ruta is None:
        check("hay al menos una ruta publicada", False, "sin rutas con histórico")
        return _resultado()

    paginas = {
        "portada": "/",
        "ficha de ruta": f"/vuelos/{ruta.origin_id}-{ruta.destination_id}/",
        "robots.txt": "/robots.txt",
        "sitemap.xml": "/sitemap.xml",
    }
    respuestas = {}
    for nombre, url in paginas.items():
        r = client.get(url, HTTP_HOST=HOST, secure=True)
        respuestas[nombre] = r
        check(f"{nombre} responde 200", r.status_code == 200, f"status={r.status_code}")

    ficha_resp = respuestas["ficha de ruta"]
    ficha = ficha_resp.content.decode()
    check(
        "la ficha declara caché de borde",
        "s-maxage" in ficha_resp.get("Cache-Control", ""),
        ficha_resp.get("Cache-Control", "sin cabecera"),
    )
    check("og:image es absoluta y https", 'property="og:image" content="https://' in ficha)
    check(
        "el documento está completo",
        ficha.startswith("<!doctype html>") and '<html lang="es">' in ficha,
    )

    print("Errores")
    r404 = client.get("/vuelos/ZZZ-ZZZ/", HTTP_HOST=HOST, secure=True)
    check("ruta desconocida da 404", r404.status_code == 404, f"status={r404.status_code}")
    check("el 404 usa la plantilla propia", "no existe" in r404.content.decode())

    return _resultado()


if __name__ == "__main__":
    raise SystemExit(main())
