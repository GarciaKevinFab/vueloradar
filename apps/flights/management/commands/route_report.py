"""Estado histórico de una ruta.

    python manage.py route_report LIM CUZ
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from apps.flights.models import FlightOffer, PriceSnapshot, Route, RouteStats

console = Console()

RECENT_SNAPSHOTS = 10


class Command(BaseCommand):
    help = "Muestra el histórico y las estadísticas acumuladas de una ruta."

    def add_arguments(self, parser):
        parser.add_argument("origin", type=str, help="IATA de origen, p. ej. LIM")
        parser.add_argument("destination", type=str, help="IATA de destino, p. ej. CUZ")
        parser.add_argument(
            "--limit", type=int, default=RECENT_SNAPSHOTS,
            help=f"cuántos snapshots recientes mostrar (default {RECENT_SNAPSHOTS})",
        )

    def handle(self, *args, **options):
        origin = options["origin"].strip().upper()
        destination = options["destination"].strip().upper()

        try:
            route = Route.objects.select_related("origin", "destination").get(
                origin_id=origin, destination_id=destination
            )
        except Route.DoesNotExist as exc:
            raise CommandError(
                f"La ruta {origin} a {destination} no existe. "
                f"Corre `manage.py load_routes` o revisa los códigos IATA."
            ) from exc

        _render_header(route)
        _render_stats(route)
        _render_recent(route, options["limit"])
        _render_all_time_low(route)


def _render_header(route: Route) -> None:
    console.print(
        Panel(
            f"[bold cyan]{route.origin.city} ({route.origin_id})[/bold cyan] → "
            f"[bold cyan]{route.destination.city} ({route.destination_id})[/bold cyan]\n"
            f"monitoreada: {'sí' if route.is_monitored else 'no'} · "
            f"prioridad: {route.get_priority_display()} · "
            f"directo: {'sí' if route.has_direct_flights else 'no'}",
            title="Ruta",
            expand=False,
        )
    )


def _render_stats(route: Route) -> None:
    stats = RouteStats.objects.filter(route=route).first()
    if stats is None:
        console.print(
            "\n[yellow]Sin estadísticas todavía.[/yellow] "
            "Corre el barrido y luego `compute_route_stats`.\n"
        )
        return

    table = Table(title="Estadísticas 30 días", title_style="bold cyan", header_style="bold")
    for column in ("Promedio", "Mediana", "Percentil 25", "Mínimo", "Muestras"):
        table.add_column(column, justify="right")

    table.add_row(
        _money(stats.avg_30d), _money(stats.median_30d), _money(stats.p25_30d),
        _money(stats.min_30d), str(stats.samples_count),
    )
    console.print()
    console.print(table)

    if not stats.has_enough_history:
        muestras = "muestra" if stats.samples_count == 1 else "muestras"
        console.print(
            f"[yellow]Ojo:[/yellow] solo {stats.samples_count} {muestras}. "
            f"Se necesitan 10 o más para que el promedio sea confiable."
        )
    console.print(f"[dim]Actualizado: {timezone.localtime(stats.updated_at):%Y-%m-%d %H:%M}[/dim]")


def _render_recent(route: Route, limit: int) -> None:
    snapshots = list(
        PriceSnapshot.objects.filter(route=route).order_by("-snapshot_at")[:limit]
    )
    if not snapshots:
        console.print("\n[yellow]Sin snapshots todavía para esta ruta.[/yellow]")
        return

    table = Table(
        title=f"Últimos {len(snapshots)} snapshots", title_style="bold cyan", header_style="bold"
    )
    table.add_column("Tomado", justify="center")
    table.add_column("Fecha vuelo", justify="center")
    table.add_column("Mínimo S/", justify="right")
    table.add_column("Promedio S/", justify="right")
    table.add_column("Ofertas", justify="right")
    table.add_column("Más barata")

    for snapshot in snapshots:
        table.add_row(
            f"{timezone.localtime(snapshot.snapshot_at):%d/%m %H:%M}",
            snapshot.flight_date.strftime("%d/%m/%Y"),
            _money(snapshot.min_price_pen),
            _money(snapshot.avg_price_pen),
            str(snapshot.offers_count),
            snapshot.cheapest_airline or "—",
        )

    console.print()
    console.print(table)


def _render_all_time_low(route: Route) -> None:
    best = PriceSnapshot.objects.filter(route=route).order_by("min_price_pen").first()
    if best is None:
        return

    console.print(
        f"\n[bold green]Mínimo histórico:[/bold green] S/ {best.min_price_pen:,.2f} "
        f"con {best.cheapest_airline or 'aerolínea desconocida'} "
        f"para volar el {best.flight_date:%d/%m/%Y} "
        f"[dim](visto el {timezone.localtime(best.snapshot_at):%d/%m/%Y %H:%M})[/dim]"
    )

    offers = FlightOffer.objects.filter(route=route).count()
    snapshots = PriceSnapshot.objects.filter(route=route).count()
    console.print(f"[dim]{snapshots} snapshots · {offers} ofertas crudas en la base[/dim]")


def _money(value) -> str:
    return "—" if value is None else f"{value:,.2f}"
