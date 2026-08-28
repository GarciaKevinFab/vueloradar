"""Fotos de ciudad para las fichas de ruta y los hubs.

Las genera `scripts/build_city_photos.py` desde Unsplash y quedan versionadas.
Acá vive solo el mapa: qué archivo le toca a cada ciudad, quién la tomó y qué
dice el texto alternativo.

**Solo hay foto de cinco ciudades, y eso es deliberado.** Para las demás, la
búsqueda no devolvía una imagen que fuera verificablemente del lugar — y una
foto equivocada es peor que ninguna en un sitio cuyo único activo es que se le
crea lo que afirma. Las ciudades sin foto simplemente no la muestran.

El texto alternativo describe lo que se ve, no repite el nombre de la ciudad:
un lector de pantalla ya leyó el título justo encima, y repetirlo es ruido.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Foto:
    """Una foto de ciudad, con su crédito."""

    #: Ruta dentro de los estáticos.
    archivo: str
    autor: str
    alt: str

    #: Las bandas se generan todas al mismo tamaño; el tamaño va en el HTML
    #: para que el navegador reserve el espacio y la página no salte al cargar.
    ancho: int = 1200
    alto: int = 400


FOTOS: dict[str, Foto] = {
    "lima": Foto(
        archivo="web/ciudades/lima.webp",
        autor="Willian Justen de Vasconcellos",
        alt="El malecón de Miraflores sobre los acantilados y el mar, al atardecer",
    ),
    "cusco": Foto(
        archivo="web/ciudades/cusco.webp",
        autor="Willian Justen de Vasconcellos",
        alt="Las ruinas de Machu Picchu entre montañas y niebla",
    ),
    "arequipa": Foto(
        archivo="web/ciudades/arequipa.webp",
        autor="Megan Kotlus",
        alt="El volcán Misti nevado detrás de la torre de la catedral",
    ),
    "iquitos": Foto(
        archivo="web/ciudades/iquitos.webp",
        autor="EcoNaturalist.com",
        alt="La selva amazónica sobre un río de aguas marrones",
    ),
    "juliaca": Foto(
        archivo="web/ciudades/juliaca.webp",
        autor="Jeison Higuita",
        alt="Las islas flotantes de totora de los Uros en el lago Titicaca",
    ),
}


def foto_de(airport) -> Foto | None:
    """La foto de la ciudad de un aeropuerto, o `None` si no tenemos.

    Devolver `None` es un resultado normal, no un error: la plantilla no dibuja
    nada y la página se ve igual de bien sin la banda.
    """
    if airport is None:
        return None
    return FOTOS.get(airport.slug)
