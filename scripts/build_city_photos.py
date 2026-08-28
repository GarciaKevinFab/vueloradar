"""Descarga y optimiza las fotos de ciudad que ilustran las fichas.

    python scripts/build_city_photos.py

Fuente: Unsplash, licencia libre. **Solo `images.unsplash.com`**: lo que vive
en `plus.unsplash.com` es Unsplash+, que es de pago, y no se puede usar acá.

Salida: `apps/web/static/web/ciudades/<slug>.webp`, recortadas a una banda
apaisada. Se corre a mano y los resultados se versionan, igual que
`build_brand_assets.py`; por eso Pillow no está en `requirements.txt`.

**Cada foto se verificó a ojo** antes de entrar acá: una imagen mal atribuida
—la plaza de otra ciudad, un paisaje de otro país— es peor que no tener foto,
porque el sitio vive de que se le crea lo que afirma.

Se descartaron a propósito:

* Piura y Máncora: la búsqueda devuelve platos de comida, no la costa.
* Belén (Iquitos): fotografía documental con personas identificables de un
  barrio humilde. Como banner decorativo de un sitio de vuelos convierte a esa
  gente en paisaje. Se usó la selva sobre el río en su lugar.
* La catedral de Arequipa bajo el arco: excelente foto, pero es vertical y en
  una banda apaisada se pierde entera.
"""

from __future__ import annotations

import sys
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "apps" / "web" / "static" / "web" / "ciudades"

#: Proporción de la banda. 3:1 deja una franja que acompaña sin tapar el dato,
#: que es lo que la persona vino a ver.
ANCHO, ALTO = 1200, 400

#: slug de la ciudad -> (id de la foto en Unsplash, autor)
#: El autor no lo exige la licencia, pero se acredita igual: son fotos que
#: alguien regaló y cuesta una línea de HTML.
FOTOS: dict[str, tuple[str, str]] = {
    "lima":     ("photo-1531968455001-5c5272a41129", "Willian Justen de Vasconcellos"),
    "cusco":    ("photo-1526392060635-9d6019884377", "Willian Justen de Vasconcellos"),
    "arequipa": ("photo-1590545651636-f0e7f151239f", "Megan Kotlus"),
    "iquitos":  ("photo-1774830970925-e176508dc0ab", "EcoNaturalist.com"),
    "juliaca":  ("photo-1553550765-41e7dff2bd41", "Jeison Higuita"),
}


def descargar(photo_id: str) -> Image.Image:
    url = f"https://images.unsplash.com/{photo_id}?w=2000&q=80&fm=jpg&fit=crop"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return Image.open(BytesIO(resp.read())).convert("RGB")


def recortar_banda(img: Image.Image) -> Image.Image:
    """Recorta a la proporción de la banda tomando la franja central.

    El centro y no el borde: en un paisaje el interés está en el medio, y
    recortar desde arriba deja una banda de cielo vacío.
    """
    objetivo = ANCHO / ALTO
    w, h = img.size
    if w / h > objetivo:
        nuevo_w = int(h * objetivo)
        izq = (w - nuevo_w) // 2
        img = img.crop((izq, 0, izq + nuevo_w, h))
    else:
        nuevo_h = int(w / objetivo)
        arriba = (h - nuevo_h) // 2
        img = img.crop((0, arriba, w, arriba + nuevo_h))
    return img.resize((ANCHO, ALTO), Image.LANCZOS)


#: Techo por foto. Una imagen decorativa no puede pesar más que todo el resto
#: de la página junta; el tráfico es móvil peruano y esto se paga en abandono.
PRESUPUESTO = 60 * 1024


def _guardar_bajo_presupuesto(img: Image.Image, destino: Path) -> tuple[int, int]:
    """Baja la calidad hasta entrar en el presupuesto de peso.

    Calidad fija no sirve: con el mismo número, un cielo liso pesa 27 KB y el
    totoral de los Uros 130. Lo que hay que fijar es el peso, no el parámetro.
    Hay un **piso de calidad**: si una foto no entra en el presupuesto ni con
    55, se acepta que pese de más antes que publicarla empastada. Veinte kilos
    extra se recuperan en cualquier lado; una imagen que se ve barata contamina
    todo lo demás que el sitio afirma.
    """
    for calidad in (78, 70, 62, 55):
        img.save(destino, format="WEBP", quality=calidad, method=6)
        peso = destino.stat().st_size
        if peso <= PRESUPUESTO:
            return calidad, peso
    return calidad, peso


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for slug, (photo_id, autor) in FOTOS.items():
        try:
            banda = recortar_banda(descargar(photo_id))
        except Exception as exc:  # noqa: BLE001 - un fallo de red no debe dejar a medias
            print(f"  {slug}: no se pudo descargar ({exc})", file=sys.stderr)
            continue

        destino = OUT / f"{slug}.webp"
        calidad, peso = _guardar_bajo_presupuesto(banda, destino)
        total += peso
        print(f"  {slug:<12} {ANCHO}x{ALTO}  {peso // 1024:>3} KB  "
              f"(calidad {calidad})  · foto de {autor}")

    print(f"\n  {len(FOTOS)} ciudades, {total // 1024} KB en total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
