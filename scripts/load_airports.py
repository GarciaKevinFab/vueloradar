"""Seed de los aeropuertos monitoreados del mercado doméstico peruano.

Datos oficiales (código IATA, nombre del terminal, ciudad, región). Idempotente:
volver a correrlo actualiza nombres sin duplicar filas.
"""

from __future__ import annotations

# (IATA, nombre del aeropuerto, ciudad, región)
AIRPORTS: list[tuple[str, str, str, str]] = [
    ("LIM", "Aeropuerto Internacional Jorge Chávez", "Lima", "Lima"),
    ("CUZ", "Aeropuerto Internacional Alejandro Velasco Astete", "Cusco", "Cusco"),
    ("AQP", "Aeropuerto Internacional Alfredo Rodríguez Ballón", "Arequipa", "Arequipa"),
    ("PEM", "Aeropuerto Internacional Padre Aldamiz", "Puerto Maldonado", "Madre de Dios"),
    (
        "IQT",
        "Aeropuerto Internacional Coronel FAP Francisco Secada Vignetta",
        "Iquitos",
        "Loreto",
    ),
    (
        "TPP",
        "Aeropuerto Cadete FAP Guillermo del Castillo Paredes",
        "Tarapoto",
        "San Martín",
    ),
    (
        "PIU",
        "Aeropuerto Internacional Capitán FAP Guillermo Concha Iberico",
        "Piura",
        "Piura",
    ),
    (
        "TRU",
        "Aeropuerto Internacional Capitán FAP Carlos Martínez de Pinillos",
        "Trujillo",
        "La Libertad",
    ),
    (
        "CIX",
        "Aeropuerto Internacional Capitán FAP José Abelardo Quiñones González",
        "Chiclayo",
        "Lambayeque",
    ),
    ("JUL", "Aeropuerto Internacional Inca Manco Cápac", "Juliaca", "Puno"),
    ("AYP", "Aeropuerto Coronel FAP Alfredo Mendívil Duarte", "Ayacucho", "Ayacucho"),
    ("PCL", "Aeropuerto Internacional David Abensur Rengifo", "Pucallpa", "Ucayali"),
    (
        "CJA",
        "Aeropuerto Mayor General FAP Armando Revoredo Iglesias",
        "Cajamarca",
        "Cajamarca",
    ),
    (
        "TBP",
        "Aeropuerto Internacional Capitán FAP Pedro Canga Rodríguez",
        "Tumbes",
        "Tumbes",
    ),
    ("HUU", "Aeropuerto Alférez FAP David Figueroa Fernandini", "Huánuco", "Huánuco"),
    ("JAU", "Aeropuerto Francisco Carlé", "Jauja", "Junín"),
    ("ANS", "Aeropuerto de Andahuaylas", "Andahuaylas", "Apurímac"),
    ("CHM", "Aeropuerto Teniente FAP Jaime Montreuil Morales", "Chimbote", "Áncash"),
    ("TYL", "Aeropuerto Capitán FAP Víctor Montes Arias", "Talara", "Piura"),
    ("RIM", "Aeropuerto Huayabamba", "Rodríguez de Mendoza", "Amazonas"),
]


#: Nombre con el que se busca el destino cuando no coincide con la ciudad del
#: aeropuerto. Sale de las consultas reales de Search Console («vuelos lima
#: puno»), no de la región: «Madre de Dios» o «La Libertad» no los busca nadie.
ALIASES: dict[str, str] = {
    "JUL": "Puno",
    "JAU": "Huancayo",
}


def load_airports() -> tuple[int, int]:
    """Crea o actualiza los aeropuertos. Devuelve `(creados, actualizados)`."""
    from apps.flights.models import Airport

    created = updated = 0
    for iata, name, city, region in AIRPORTS:
        _, was_created = Airport.objects.update_or_create(
            iata_code=iata,
            defaults={
                "name": name,
                "city": city,
                "region": region,
                "alias": ALIASES.get(iata, ""),
                "is_active": True,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return created, updated
