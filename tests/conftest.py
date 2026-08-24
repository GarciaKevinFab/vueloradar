"""Configuración común de la suite.

Regla dura de la Fase 1: ningún test toca la red. Los proveedores se mockean.
"""

import pytest


@pytest.fixture
def peru_airports(db):
    """Los aeropuertos mínimos para armar rutas en los tests."""
    from apps.flights.models import Airport

    data = [
        ("LIM", "Jorge Chávez", "Lima", "Lima"),
        ("CUZ", "Velasco Astete", "Cusco", "Cusco"),
        ("AQP", "Rodríguez Ballón", "Arequipa", "Arequipa"),
        ("PEM", "Padre Aldamiz", "Puerto Maldonado", "Madre de Dios"),
    ]
    return {
        iata: Airport.objects.create(iata_code=iata, name=name, city=city, region=region)
        for iata, name, city, region in data
    }
