"""Seed de las rutas monitoreadas.

El mercado peruano es hub-and-spoke: casi todo pasa por Lima. Se generan las
rutas LIM↔provincia en ambos sentidos (cada sentido es una ruta distinta) más
las pocas interprovinciales con vuelo directo real.

Idempotente: volver a correrlo re-sincroniza flags sin duplicar filas.
"""

from __future__ import annotations

HUB = "LIM"

#: Provincias con vuelo directo desde/hacia Lima.
SPOKES = [
    "CUZ", "AQP", "PEM", "IQT", "TPP", "PIU", "TRU", "CIX", "JUL", "AYP",
    "PCL", "CJA", "TBP", "HUU", "JAU", "ANS", "CHM", "TYL", "RIM",
]

#: Rutas interprovinciales con vuelo directo (sin pasar por Lima). Se expanden
#: a los dos sentidos.
INTERPROVINCIAL_PAIRS = [
    ("CUZ", "AQP"),
    ("CUZ", "PEM"),
    ("CUZ", "JUL"),
]

#: Rutas de prioridad alta del plan (CLAUDE.md §3), en ambos sentidos.
HIGH_PRIORITY_PAIRS = [
    ("LIM", "CUZ"),
    ("LIM", "AQP"),
    ("LIM", "PEM"),
    ("LIM", "IQT"),
    ("LIM", "TPP"),
    ("LIM", "PIU"),
    ("LIM", "TRU"),
    ("LIM", "JUL"),
    ("LIM", "JAU"),
    ("CUZ", "PEM"),
    ("CUZ", "AQP"),
]


def _both_directions(pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
    directed = set()
    for a, b in pairs:
        directed.add((a, b))
        directed.add((b, a))
    return directed


def build_route_specs() -> list[tuple[str, str, int]]:
    """Devuelve `(origen, destino, prioridad)` de todas las rutas a sembrar."""
    high_priority = _both_directions(HIGH_PRIORITY_PAIRS)

    directed: set[tuple[str, str]] = set()
    for spoke in SPOKES:
        directed.add((HUB, spoke))
        directed.add((spoke, HUB))
    directed |= _both_directions(INTERPROVINCIAL_PAIRS)

    specs = []
    for origin, destination in sorted(directed):
        if (origin, destination) in high_priority:
            priority = 1
        elif HUB in (origin, destination):
            priority = 2
        else:
            priority = 3
        specs.append((origin, destination, priority))

    return specs


def load_routes() -> tuple[int, int, int]:
    """Crea o actualiza las rutas. Devuelve `(creadas, actualizadas, omitidas)`.

    Omitidas = rutas cuyo aeropuerto todavía no está en la base (corre
    `load_airports` primero).
    """
    from apps.flights.models import Airport, Route

    known = set(Airport.objects.values_list("iata_code", flat=True))
    created = updated = skipped = 0

    for origin, destination, priority in build_route_specs():
        if origin not in known or destination not in known:
            skipped += 1
            continue

        _, was_created = Route.objects.update_or_create(
            origin_id=origin,
            destination_id=destination,
            defaults={
                "is_monitored": True,
                "has_direct_flights": True,
                "priority": priority,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return created, updated, skipped
