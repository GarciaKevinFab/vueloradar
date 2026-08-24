"""Formateo de los mensajes del bot, con y sin histórico de la ruta."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bot import formatting

LIMA = ZoneInfo("America/Lima")
FECHA = date(2026, 9, 15)


class FakeOffer:
    """Doble de FlightOffer: solo lo que el formateo lee."""

    def __init__(self, price, airline="LATAM", hour=6, minute=10, stops=0, pk=1):
        self.price_pen = Decimal(price)
        self.airline = airline
        self.stops = stops
        self.pk = pk
        self.departure_dt = datetime(2026, 9, 15, hour, minute, tzinfo=LIMA)
        self.arrival_dt = self.departure_dt + timedelta(hours=1, minutes=25)


class FakeStats:
    def __init__(self, avg, samples=30):
        self.avg_30d = Decimal(avg)
        self.samples_count = samples

    @property
    def has_enough_history(self):
        return self.samples_count >= 10


OFERTAS = [
    FakeOffer("129", "Sky", 6, 10),
    FakeOffer("142", "JetSmart", 9, 40),
    FakeOffer("156", "LATAM", 5, 30),
    FakeOffer("171", "LATAM", 13, 20),
    FakeOffer("189", "Sky", 19, 0),
    FakeOffer("210", "LATAM", 20, 0),
]


def test_medallas_y_numeracion():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=OFERTAS, limit=5
    )

    assert "🥇" in texto and "🥈" in texto and "🥉" in texto
    assert "4." in texto and "5." in texto
    assert "S/ 210" not in texto, "solo se muestran 5"


def test_encabezado_y_pie():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=OFERTAS
    )

    assert "<b>LIM → CUZ</b>" in texto
    assert "mar 15 set" in texto
    assert "/alerta LIM CUZ" in texto


def test_linea_de_oferta_completa():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=[FakeOffer("129", "Sky", 6, 10)]
    )

    assert "<b>S/ 129</b>" in texto
    assert "Sky" in texto
    assert "06:10→07:35" in texto
    assert "directo" in texto


# ------------------------------------------------------ contexto de precio
def test_sin_stats_se_omite_la_linea_de_promedio():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=OFERTAS, stats=None
    )

    assert "📊" not in texto
    assert "Promedio" not in texto


def test_con_pocas_muestras_tambien_se_omite():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=OFERTAS,
        stats=FakeStats("178", samples=4),
    )

    assert "📊" not in texto, "menos de 10 muestras no es histórico confiable"


def test_precio_muy_por_debajo_del_promedio():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=OFERTAS, stats=FakeStats("178")
    )

    assert "📊" in texto
    assert "S/ 178" in texto
    assert "28% por debajo" in texto  # (178-129)/178 = 27,5% -> 28
    assert "Buen momento" in texto


def test_precio_en_el_promedio():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=[FakeOffer("130")],
        stats=FakeStats("132"),
    )

    assert "en el promedio" in texto


def test_precio_por_encima_sugiere_esperar():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=[FakeOffer("250")],
        stats=FakeStats("180"),
    )

    assert "por encima" in texto
    assert "Conviene esperar" in texto


# ------------------------------------------------------------ casos borde
def test_sin_ofertas():
    texto = formatting.format_results(
        origin="LIM", dest="ANS", flight_date=FECHA, offers=[]
    )

    assert "No encontré vuelos" in texto
    assert "📊" not in texto


def test_oferta_sin_horario_no_rompe():
    oferta = FakeOffer("300")
    oferta.departure_dt = None
    oferta.arrival_dt = None

    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA, offers=[oferta]
    )

    assert "S/ 300" in texto
    assert "→07:35" not in texto


def test_itinerario_sintetico_se_marca_via_lim():
    sintetico = FakeOffer("758", "Star Peru / LATAM", stops=1, pk=None)
    texto = formatting.format_results(
        origin="HUU", dest="PEM", flight_date=FECHA, offers=[sintetico]
    )

    assert "vía LIM" in texto


def test_se_escapa_el_html_de_la_aerolinea():
    texto = formatting.format_results(
        origin="LIM", dest="CUZ", flight_date=FECHA,
        offers=[FakeOffer("100", "<script>alert(1)</script>")],
    )

    assert "<script>" not in texto
    assert "&lt;script&gt;" in texto


# ------------------------------------------------------------- vista flexible
def test_vista_flexible_marca_el_mejor_dia():
    mejores = {
        date(2026, 9, 13): Decimal("210"),
        date(2026, 9, 14): Decimal("178"),
        date(2026, 9, 15): Decimal("205"),
    }
    texto = formatting.format_flexible_results(
        origin="LIM", dest="PEM", best_by_day=mejores, target=FECHA
    )

    assert "⭐" in texto
    assert texto.count("⭐") == 1
    assert "S/ 178" in texto
    assert "/vuelo LIM PEM 2026-09-14" in texto


def test_vista_flexible_sin_resultados():
    texto = formatting.format_flexible_results(
        origin="LIM", dest="ANS", best_by_day={}, target=FECHA
    )

    assert "No encontré vuelos" in texto


# ---------------------------------------------------------------- otros
def test_mensaje_de_cupo_agotado():
    texto = formatting.quota_exceeded_message(10)

    assert "10" in texto
    assert "Premium" in texto


def test_listado_de_rutas():
    filas = [
        {"origin": "LIM", "dest": "CUZ", "origin_city": "Lima", "dest_city": "Cusco",
         "min_price": Decimal("201")},
        {"origin": "LIM", "dest": "ANS", "origin_city": "Lima", "dest_city": "Andahuaylas",
         "min_price": None},
    ]
    texto = formatting.format_routes(filas)

    assert "LIM→CUZ" in texto
    assert "S/ 201" in texto
    assert "sin datos" in texto


def test_alerta_es_placeholder():
    texto = formatting.alert_placeholder_message("LIM", "CUZ")

    assert "en camino" in texto
    assert "LIM → CUZ" in texto
