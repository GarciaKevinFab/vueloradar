"""Imagen de compartir por ruta y por ciudad.

    python scripts/build_og_images.py

Es lo que ve alguien cuando le pegan el enlace por WhatsApp. Hasta ahora todas
las páginas compartían la misma imagen genérica, así que un enlace a Lima–Cusco
se veía igual que uno a la portada y no decía nada.

**Sin precio, a propósito.** Poner "desde S/ 168" sería más vistoso, pero la
imagen se genera una vez y el precio cambia dos veces por día: alguien
compartiría un número que ya no existe y quien hace clic vería otro. En un
producto cuyo activo es que se le crea el precio, eso es lo peor que podría
pasar. La imagen dice de qué ruta se trata; el precio lo dice la página.

Se genera offline y se versiona, como `build_brand_assets.py`, así que Pillow
no entra en producción ni hay CPU por petición.
"""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

import django
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.flights.models import Route  # noqa: E402
from apps.web import queries  # noqa: E402

OUT = BASE / "apps" / "web" / "static" / "web" / "og"
MODULO = BASE / "apps" / "web" / "og_images.py"

TAMANO = (1200, 630)
LOGO = BASE / "apps" / "web" / "static" / "web" / "icon-512.png"

TINTA = (20, 22, 26)
TENUE = (95, 99, 104)
LINEA = (227, 224, 218)
ACENTO = (0, 84, 212)
PAPEL = (255, 255, 255)

#: Pillow no lee woff2, así que las fuentes propias del sitio no sirven acá y
#: se cae a las del sistema. El resultado es igual de legible en una miniatura.
FUENTES_TITULO = [Path("C:/Windows/Fonts/georgia.ttf"), Path("C:/Windows/Fonts/times.ttf")]
FUENTES_TEXTO = [Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/arial.ttf")]
FUENTES_FUERTE = [Path("C:/Windows/Fonts/segoeuib.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")]


def fuente(candidatas, tamano: int):
    for ruta in candidatas:
        if ruta.exists():
            return ImageFont.truetype(str(ruta), tamano)
    return ImageFont.load_default(tamano)


def _ancho(d: ImageDraw.ImageDraw, texto: str, f) -> int:
    return int(d.textlength(texto, font=f))


def lienzo() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", TAMANO, PAPEL)
    d = ImageDraw.Draw(img)
    # Franja de acento arriba: identifica la marca sin ocupar espacio.
    d.rectangle([0, 0, TAMANO[0], 10], fill=ACENTO)
    return img, d


def _marca(img: Image.Image, d: ImageDraw.ImageDraw) -> None:
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA").resize((64, 64), Image.LANCZOS)
        img.paste(logo, (80, TAMANO[1] - 132), logo)
    d.text((160, TAMANO[1] - 124), "VueloRadar", font=fuente(FUENTES_FUERTE, 34), fill=TINTA)
    d.text((160, TAMANO[1] - 84), "Histórico real de precios",
           font=fuente(FUENTES_TEXTO, 24), fill=TENUE)


def _ajustar(d: ImageDraw.ImageDraw, texto: str, reserva: int = 0):
    """La fuente más grande con la que el texto entra. Nunca se recorta una
    ciudad: «Puerto Maldonado» tiene que leerse igual que «Lima»."""
    tam = 92
    f = fuente(FUENTES_TITULO, tam)
    while _ancho(d, texto, f) > TAMANO[0] - 160 - reserva and tam > 44:
        tam -= 4
        f = fuente(FUENTES_TITULO, tam)
    return f


def _titulo(d: ImageDraw.ImageDraw, lineas: list[str], y: int) -> None:
    """Título en serif, encogiendo si no entra."""
    for linea in lineas:
        f = _ajustar(d, linea)
        d.text((80, y), linea, font=f, fill=TINTA)
        y += int(f.size * 1.15)


def _flecha(d: ImageDraw.ImageDraw, x: int, y: int, largo: int = 68) -> None:
    """La flecha, dibujada y no escrita.

    Georgia —y la mayoría de las serif clásicas— no trae el glifo `→`, así que
    escribirlo deja un cuadro vacío en la imagen que se comparte. Dibujarla con
    dos primitivas no depende de ninguna tipografía.
    """
    grosor = 7
    d.line([x, y, x + largo - 18, y], fill=TINTA, width=grosor)
    d.polygon(
        [(x + largo, y), (x + largo - 24, y - 15), (x + largo - 24, y + 15)],
        fill=TINTA,
    )


def imagen_de_ruta(origen, destino) -> Image.Image:
    img, d = lienzo()
    d.text((80, 96), "¿ES BUEN PRECIO HOY?", font=fuente(FUENTES_TEXTO, 26), fill=ACENTO)

    # La flecha va a la derecha del origen, en su misma linea: debajo chocaria
    # con la inicial del destino. Se reserva su ancho al ajustar la fuente.
    ancho_flecha = 68 + 28
    y = 150
    f_origen = _ajustar(d, origen.city, reserva=ancho_flecha)
    d.text((80, y), origen.city, font=f_origen, fill=TINTA)
    _flecha(d, 80 + _ancho(d, origen.city, f_origen) + 28, y + int(f_origen.size * 0.52))

    y += int(f_origen.size * 1.15)
    f_destino = _ajustar(d, destino.city)
    d.text((80, y), destino.city, font=f_destino, fill=TINTA)
    d.text((80, 384), f"{origen.iata_code} · {destino.iata_code}",
           font=fuente(FUENTES_TEXTO, 28), fill=TENUE)
    d.line([80, 440, TAMANO[0] - 80, 440], fill=LINEA, width=2)
    _marca(img, d)
    return img


def imagen_de_ciudad(airport, destinos: int) -> Image.Image:
    img, d = lienzo()
    d.text((80, 96), "VUELOS DESDE", font=fuente(FUENTES_TEXTO, 26), fill=ACENTO)
    _titulo(d, [airport.city], 150)
    d.text((80, 300), f"{destinos} destino{'s' if destinos != 1 else ''} con histórico propio",
           font=fuente(FUENTES_TEXTO, 30), fill=TENUE)
    d.line([80, 440, TAMANO[0] - 80, 440], fill=LINEA, width=2)
    _marca(img, d)
    return img


def _guardar(img: Image.Image, destino: Path) -> int:
    """Paleta si achica, color completo si no. Son diseños planos: casi siempre
    gana la paleta, y por bastante."""
    completo, paleta = BytesIO(), BytesIO()
    img.save(completo, format="PNG", optimize=True)
    img.quantize(colors=256, method=Image.Quantize.FASTOCTREE).save(
        paleta, format="PNG", optimize=True)
    mejor = paleta if paleta.tell() < completo.tell() else completo
    destino.write_bytes(mejor.getvalue())
    return destino.stat().st_size


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rutas = list(queries.published_routes())
    if not rutas:
        print("No hay rutas publicadas todavía.", file=sys.stderr)
        return 1

    claves_ruta, claves_ciudad, total = [], [], 0

    for route in rutas:
        clave = f"{route.origin_id}-{route.destination_id}"
        total += _guardar(imagen_de_ruta(route.origin, route.destination),
                          OUT / f"{clave}.png")
        claves_ruta.append(clave)

    publicados = {r.destination_id for r in rutas}
    for airport in queries.cities_with_routes():
        cuantos = Route.objects.filter(
            origin=airport, is_monitored=True, destination_id__in=publicados
        ).count()
        total += _guardar(imagen_de_ciudad(airport, cuantos),
                          OUT / f"desde-{airport.slug}.png")
        claves_ciudad.append(airport.slug)

    MODULO.write_text(
        '"""Claves con imagen de compartir generada.\n\n'
        "GENERADO por `scripts/build_og_images.py`. No editar a mano.\n\n"
        "Existe para que la plantilla sepa si hay imagen sin tocar el disco en\n"
        "cada peticion: `{% static %}` con manifiesto revienta si el archivo no\n"
        'esta, asi que preguntar antes es la diferencia entre una pagina y un 500.\n"""\n\n'
        f"RUTAS = frozenset({sorted(claves_ruta)!r})\n\n"
        f"CIUDADES = frozenset({sorted(claves_ciudad)!r})\n",
        encoding="utf-8",
    )

    piezas = len(claves_ruta) + len(claves_ciudad)
    print(f"  {len(claves_ruta)} rutas + {len(claves_ciudad)} ciudades")
    print(f"  {total // 1024} KB en total, {total // 1024 // max(piezas, 1)} KB de promedio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
