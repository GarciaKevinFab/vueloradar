"""Traduce lenguaje natural a una intención de búsqueda de vuelo.

"vuelo de lima a puerto maldonado el 20 de setiembre"
    -> FlightIntent(origin='LIM', dest='PEM', date=2026-09-20, flexible_days=0)

La tabla ciudad→IATA se arma desde la base, no se hardcodea: si mañana se
agrega un aeropuerto, el parser lo entiende sin tocar el prompt.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .llm_router import complete_json
from .prompts import NL_PARSER_SYSTEM

logger = logging.getLogger(__name__)

CACHE_PREFIX = "nl_parser:"

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

#: Ventana razonable para un vuelo: Google no vende más allá de ~11 meses.
MAX_FUTURE_DAYS = 330


@dataclass(frozen=True)
class FlightIntent:
    """Lo que el usuario quiso pedir, ya normalizado."""

    is_flight_search: bool
    origin: str | None = None
    dest: str | None = None
    date: date | None = None
    return_date: date | None = None
    flexible_days: int = 0

    @property
    def is_complete(self) -> bool:
        """Tiene todo lo necesario para lanzar una búsqueda."""
        return bool(self.is_flight_search and self.origin and self.dest and self.date)

    @property
    def is_round_trip(self) -> bool:
        """Hay vuelta, y es posterior a la ida."""
        return bool(self.return_date and self.date and self.return_date > self.date)

    @property
    def missing(self) -> list[str]:
        """Qué le falta, en español, para poder pedírselo al usuario."""
        faltantes = []
        if not self.origin:
            faltantes.append("el origen")
        if not self.dest:
            faltantes.append("el destino")
        if not self.date:
            faltantes.append("la fecha")
        return faltantes


NOT_A_SEARCH = FlightIntent(is_flight_search=False)


def parse_flight_request(message: str, *, today: date | None = None) -> FlightIntent:
    """Extrae la intención de un mensaje libre. Nunca lanza excepción.

    Ante cualquier fallo del proveedor devuelve una intención vacía, para que
    el bot pida el formato explícito en vez de romperse.
    """
    texto = (message or "").strip()
    if not texto:
        return NOT_A_SEARCH

    today = today or timezone.localdate()
    clave = _cache_key(texto, today)

    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado

    airports = _airports_table()
    if not airports:
        logger.error("nl_parser: no hay aeropuertos cargados, corre load_airports")
        return NOT_A_SEARCH

    system = NL_PARSER_SYSTEM.format(
        airports=airports,
        today=today.isoformat(),
        weekday=DIAS[today.weekday()],
    )

    resultado = complete_json(system, texto, max_tokens=settings.ANTHROPIC_MAX_TOKENS)
    if resultado is None:
        logger.warning("nl_parser: ningún proveedor de IA respondió")
        return NOT_A_SEARCH

    crudo, proveedor = resultado
    logger.debug("nl_parser: intención extraída por %s", proveedor)

    intent = _normalize(crudo, today)
    _cache_set(clave, intent)
    return intent


# --------------------------------------------------------------- normalización
def _normalize(crudo: dict, today: date) -> FlightIntent:
    """Valida lo que devolvió el modelo contra la realidad de la base."""
    if not crudo.get("is_flight_search"):
        return NOT_A_SEARCH

    validos = _valid_iata_codes()
    origin = _clean_iata(crudo.get("origin_iata"), validos)
    dest = _clean_iata(crudo.get("dest_iata"), validos)
    if origin and dest and origin == dest:
        # El modelo se confundió; mejor pedir aclaración que buscar LIM->LIM.
        origin = dest = None

    fecha = _clean_date(crudo.get("date"), today)

    # La vuelta solo vale si hay ida y es posterior. Una fecha de regreso
    # anterior a la de salida es un error de lectura del modelo, no un viaje.
    vuelta = _clean_date(crudo.get("return_date"), today)
    if vuelta and fecha and vuelta <= fecha:
        logger.info("nl_parser: fecha de vuelta %s no posterior a la ida, se descarta", vuelta)
        vuelta = None

    flexible = crudo.get("flexible_days") or 0
    try:
        flexible = max(0, min(int(flexible), settings.BOT_MAX_FLEXIBLE_DAYS))
    except (TypeError, ValueError):
        flexible = 0

    return FlightIntent(
        is_flight_search=True,
        origin=origin,
        dest=dest,
        date=fecha,
        return_date=vuelta,
        flexible_days=flexible,
    )


def _clean_iata(value, validos: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if code in validos else None


def _clean_date(value, today: date) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        fecha = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    if fecha < today or fecha > today + timedelta(days=MAX_FUTURE_DAYS):
        return None
    return fecha


# --------------------------------------------------------------------- helpers
def _airports_table() -> str:
    """Lista IATA - ciudad para meterle al prompt, cacheada por proceso."""
    from apps.flights.models import Airport

    filas = Airport.objects.filter(is_active=True).order_by("iata_code").values_list(
        "iata_code", "city"
    )
    return "\n".join(f"- {code}: {city}" for code, city in filas)


def _valid_iata_codes() -> set[str]:
    from apps.flights.models import Airport

    return set(Airport.objects.filter(is_active=True).values_list("iata_code", flat=True))


def _cache_key(texto: str, today: date) -> str:
    """La fecha entra en la clave: "mañana" no significa lo mismo cada día."""
    digest = hashlib.sha256(texto.lower().encode("utf-8")).hexdigest()[:32]
    return f"{CACHE_PREFIX}{today.isoformat()}:{digest}"


def _cache_get(clave: str) -> FlightIntent | None:
    try:
        crudo = cache.get(clave)
    except Exception as exc:  # noqa: BLE001 - Redis caído no rompe el bot
        logger.warning("nl_parser: cache inaccesible: %s", exc)
        return None
    if not crudo:
        return None
    fecha = crudo.get("date")
    return FlightIntent(
        is_flight_search=crudo["is_flight_search"],
        origin=crudo.get("origin"),
        dest=crudo.get("dest"),
        date=date.fromisoformat(fecha) if fecha else None,
        return_date=(
            date.fromisoformat(crudo["return_date"]) if crudo.get("return_date") else None
        ),
        flexible_days=crudo.get("flexible_days", 0),
    )


def _cache_set(clave: str, intent: FlightIntent) -> None:
    try:
        cache.set(
            clave,
            {
                "is_flight_search": intent.is_flight_search,
                "origin": intent.origin,
                "dest": intent.dest,
                "date": intent.date.isoformat() if intent.date else None,
                "return_date": intent.return_date.isoformat() if intent.return_date else None,
                "flexible_days": intent.flexible_days,
            },
            settings.NL_PARSER_CACHE_TTL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("nl_parser: no se pudo cachear: %s", exc)
