"""Búsqueda de vuelos por CLI.

    python manage.py search_flights LIM CUZ 2026-09-15
"""

from __future__ import annotations

from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from rich.console import Console
from rich.table import Table

from apps.scraping.services import UnknownAirportError, search_and_store

console = Console()


class Command(BaseCommand):
    help = "Busca los vuelos más baratos de una ruta y fecha, y los guarda en la base."

    def add_arguments(self, parser):
        parser.add_argument("origin", type=str, help="IATA de origen, p. ej. LIM")
        parser.add_argument("destination", type=str, help="IATA de destino, p. ej. CUZ")
        parser.add_argument("date", type=str, help="Fecha del vuelo en formato YYYY-MM-DD")

    def handle(self, *args, **options):
        origin = options["origin"].strip().upper()
        destination = options["destination"].strip().upper()
        search_date = _parse_date(options["date"])

        console.print(
            f"\n[bold]Buscando[/bold] {origin} → {destination} "
            f"para el [bold]{search_date.isoformat()}[/bold]…\n"
        )

        try:
            offers = search_and_store(origin, destination, search_date)
        except UnknownAirportError as exc:
            raise CommandError(str(exc)) from exc

        if not offers:
            console.print(
                "[yellow]Sin resultados.[/yellow] No se encontraron vuelos directos ni "
                "conexiones vía LIM para esa ruta y fecha."
            )
            return

        _render_table(offers, origin, destination)
        _render_footer(offers)


def _parse_date(raw: str) -> date:
    try:
        parsed = datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"Fecha inválida: {raw!r}. Usa el formato YYYY-MM-DD.") from exc

    if parsed < timezone.localdate():
        raise CommandError("La fecha del vuelo ya pasó.")
    return parsed


def _render_table(offers, origin: str, destination: str) -> None:
    table = Table(
        title=f"Vuelos {origin} → {destination}",
        title_style="bold cyan",
        header_style="bold",
    )
    table.add_column("Salida", justify="center")
    table.add_column("Llegada", justify="center")
    table.add_column("Aerolínea")
    table.add_column("Escalas", justify="center")
    table.add_column("Precio S/", justify="right")
    table.add_column("Fuente", style="dim")

    cheapest = min(offer.price_pen for offer in offers)

    for offer in offers:
        is_cheapest = offer.price_pen == cheapest
        style = "bold green" if is_cheapest else None
        table.add_row(
            _format_time(offer.departure_dt),
            _format_time(offer.arrival_dt),
            offer.airline or "—",
            _format_stops(offer),
            f"{offer.price_pen:,.2f}",
            offer.get_source_display(),
            style=style,
        )

    console.print(table)


def _render_footer(offers) -> None:
    best = min(offers, key=lambda offer: offer.price_pen)
    stored = sum(1 for offer in offers if offer.pk is not None)
    synthetic = len(offers) - stored

    console.print(
        f"\n[bold green]Más barato:[/bold green] S/ {best.price_pen:,.2f} "
        f"con {best.airline or 'aerolínea desconocida'} "
        f"({_format_time(best.departure_dt)} → {_format_time(best.arrival_dt)})"
    )
    console.print(f"[dim]Ofertas guardadas en la base: {stored}[/dim]")
    if synthetic:
        plural = "itinerario armado" if synthetic == 1 else "itinerarios armados"
        console.print(
            f"[dim]{synthetic} {plural} con conexión vía LIM (no se guardan; "
            f"sí se guardaron los tramos que los componen).[/dim]"
        )
    if best.deep_link:
        console.print(f"[dim]Ver en Google Flights: {best.deep_link}[/dim]")


def _format_time(value) -> str:
    if value is None:
        return "—"
    return timezone.localtime(value).strftime("%H:%M")


def _format_stops(offer) -> str:
    if offer.stops is None:
        return "—"
    if offer.stops == 0:
        return "directo"
    if offer.pk is None:
        # Itinerario sintético armado por el servicio combinando dos tramos.
        return f"{offer.stops} (vía LIM)"
    return f"{offer.stops} escala" if offer.stops == 1 else f"{offer.stops} escalas"
