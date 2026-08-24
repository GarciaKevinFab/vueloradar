"""Contrato común de los proveedores de precios.

Cada fuente (Google Flights, Sky, JetSmart...) es un plugin intercambiable
detrás de esta interfaz. Si una fuente cambia su formato, solo se toca su
módulo: el resto del sistema sigue hablando `RawFlightOffer`.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

# Símbolos y códigos de moneda que devuelven las fuentes, normalizados a ISO-4217.
CURRENCY_TOKENS = {
    "S/": "PEN",
    "S/.": "PEN",
    "PEN": "PEN",
    "SOLES": "PEN",
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
}

# Captura el primer número del string admitiendo separadores de miles y decimales.
_NUMBER_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")


class PriceParseError(ValueError):
    """El string de precio no contiene un importe reconocible."""


def parse_price(raw: str | int | float | Decimal, default_currency: str = "PEN") -> tuple[Decimal, str]:
    """Convierte un precio crudo en `(monto, moneda_iso)`.

    Tolera lo que devuelven las fuentes: ``"$120"``, ``"S/ 450"``,
    ``"PEN 1,203.50"``, ``"1.203,50 €"``, ``"US$ 89"`` o un número pelado.

    Args:
        raw: precio tal como vino de la fuente.
        default_currency: moneda a asumir si el string no la declara.

    Raises:
        PriceParseError: si no hay ningún número en el string.
    """
    if isinstance(raw, (int, float, Decimal)):
        return _to_decimal(str(raw)), default_currency

    text = str(raw).strip().replace("\xa0", " ").replace("\u202f", " ")
    if not text:
        raise PriceParseError("precio vacío")

    upper = text.upper()
    currency = default_currency
    # El orden importa: "US$" debe ganarle a "$", y "S/." a "S/".
    for token in sorted(CURRENCY_TOKENS, key=len, reverse=True):
        if token in upper:
            currency = CURRENCY_TOKENS[token]
            break

    match = _NUMBER_RE.search(text)
    if not match:
        raise PriceParseError(f"sin importe reconocible en {raw!r}")

    return _to_decimal(match.group(0)), currency


def _to_decimal(number: str) -> Decimal:
    """Normaliza separadores de miles/decimales al formato de Decimal.

    Asume que el último separador es el decimal solo si deja 1 o 2 dígitos
    detrás; en cualquier otro caso todos los separadores son de miles
    (``"1,203"`` → 1203, ``"1,203.50"`` → 1203.50, ``"1.203,50"`` → 1203.50).
    """
    cleaned = number.strip()
    last_sep = max(cleaned.rfind(","), cleaned.rfind("."))

    if last_sep == -1:
        normalized = cleaned
    else:
        decimals = len(cleaned) - last_sep - 1
        if decimals in (1, 2):
            integer_part = re.sub(r"[.,]", "", cleaned[:last_sep])
            normalized = f"{integer_part}.{cleaned[last_sep + 1:]}"
        else:
            normalized = re.sub(r"[.,]", "", cleaned)

    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation as exc:  # pragma: no cover - defensivo
        raise PriceParseError(f"importe inválido: {number!r}") from exc


@dataclass
class RawFlightOffer:
    """Oferta tal como la entrega un proveedor, antes de persistirse."""

    origin: str
    destination: str
    search_date: date
    price_pen: Decimal
    source: str
    airline: str = ""
    flight_number: str = ""
    departure_dt: Optional[datetime] = None
    arrival_dt: Optional[datetime] = None
    stops: Optional[int] = None
    original_price: Optional[Decimal] = None
    original_currency: str = ""
    deep_link: str = ""
    # Tramos que componen la oferta; solo se usa en itinerarios sintéticos vía hub.
    legs: list["RawFlightOffer"] = field(default_factory=list)

    @property
    def dedupe_key(self) -> tuple:
        """Identidad del vuelo: mismo vuelo físico = misma clave."""
        return (
            (self.airline or "").strip().lower(),
            (self.flight_number or "").strip().upper(),
            self.departure_dt,
        )


class FlightProvider(ABC):
    """Fuente de precios. Nunca lanza excepciones al caller."""

    source_name: str

    @abstractmethod
    def search(self, origin: str, dest: str, date: date) -> list[RawFlightOffer]:
        """Devuelve las ofertas encontradas, o `[]` si la fuente falló."""
        raise NotImplementedError
