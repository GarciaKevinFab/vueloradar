"""Deriva los iconos y la imagen de compartir a partir del logo original.

    python scripts/build_brand_assets.py

Fuente: `brand/logo-source.png` (el logo definitivo, cuadrado).
Salida: `apps/web/static/web/` — iconos con esquinas transparentes y la
imagen Open Graph que ve quien recibe el enlace por WhatsApp.

Se corre a mano cuando cambia el logo, no en cada deploy: los resultados se
versionan. Por eso Pillow no está en `requirements.txt`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
SOURCE = BASE / "brand" / "logo-source.png"
OUT = BASE / "apps" / "web" / "static" / "web"

#: Azul de marca, muestreado del logo original.
BRAND = (0, 84, 212)
INK = (20, 23, 26)
MUTED = (95, 99, 104)

ICON_SIZES = {
    "icon-512.png": 512,
    "icon-192.png": 192,
    "apple-touch-icon.png": 180,
    "favicon-32.png": 32,
}

OG_SIZE = (1200, 630)
#: Windows trae Segoe UI; si falta, Pillow cae a su tipografía por defecto.
FONT_BOLD = [
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
]
FONT_REGULAR = [
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
]


def _is_brand(pixel) -> bool:
    """¿El píxel es el azul del logo y no el blanco de la esquina recortada?"""
    r, g, b = pixel[:3]
    return b > 150 and r < 120 and g < 160


def detect_radius(img: Image.Image) -> int:
    """Radio de las esquinas redondeadas, leído del propio logo.

    Recorre la fila superior hasta el primer píxel de marca: esa distancia es,
    con buena aproximación, el radio. Así no queda un número codificado que se
    desactualice si cambia el logo.
    """
    w, _ = img.size
    for x in range(w // 2):
        if _is_brand(img.getpixel((x, 1))):
            return x
    return int(w * 0.2)


def rounded_alpha(size: int, radius: int) -> Image.Image:
    """Máscara con las esquinas recortadas, para que el icono no lleve blanco."""
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def load_font(candidates, size: int):
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _tile(source: Image.Image, lado: int, escala: float) -> Image.Image:
    """El logo a un tamaño dado, con las esquinas transparentes."""
    tile = source.resize((lado, lado), Image.LANCZOS).convert("RGBA")
    tile.putalpha(rounded_alpha(lado, max(1, round(lado * escala))))
    return tile


def build_icons(source: Image.Image, escala: float) -> list[str]:
    hechos = []
    for name, size in ICON_SIZES.items():
        _tile(source, size, escala).save(OUT / name, optimize=True)
        hechos.append(f"{name} ({size}x{size})")

    # El .ico lleva los dos tamaños que piden los navegadores.
    _tile(source, 64, escala).save(OUT / "favicon.ico", sizes=[(32, 32), (16, 16)])
    hechos.append("favicon.ico (32+16)")
    return hechos


def build_og(source: Image.Image, escala: float) -> str:
    """Imagen de compartir: marca a la izquierda, nombre y promesa a la derecha.

    Fondo blanco y no azul: el logo ya es un bloque azul y sobre azul se
    fundiría en la miniatura de WhatsApp.
    """
    lienzo = Image.new("RGB", OG_SIZE, (255, 255, 255))
    lado = 300
    marca = _tile(source, lado, escala)
    lienzo.paste(marca, (90, (OG_SIZE[1] - lado) // 2), marca)

    d = ImageDraw.Draw(lienzo)
    x = 90 + lado + 60
    d.text((x, 232), "VueloRadar", font=load_font(FONT_BOLD, 76), fill=INK)
    d.text((x, 328), "¿El precio de hoy es bueno?", font=load_font(FONT_REGULAR, 36), fill=MUTED)
    d.text((x, 378), "Histórico real de vuelos en Perú", font=load_font(FONT_REGULAR, 36), fill=MUTED)

    lienzo.save(OUT / "og.png", optimize=True)
    return f"og.png ({OG_SIZE[0]}x{OG_SIZE[1]})"


def main() -> int:
    if not SOURCE.exists():
        print(f"No encuentro el logo en {SOURCE}", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    source = Image.open(SOURCE).convert("RGB")
    radius = detect_radius(source)
    escala = radius / source.size[0]
    print(f"origen: {SOURCE.name} {source.size} · radio detectado: {radius}px ({escala:.1%})")

    for linea in build_icons(source, escala):
        print(f"  generado {linea}")
    print(f"  generado {build_og(source, escala)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
