from django.core.management.base import BaseCommand

from scripts.load_routes import load_routes


class Command(BaseCommand):
    help = "Carga las rutas monitoreadas (LIM<->provincias + interprovinciales directas)."

    def handle(self, *args, **options):
        created, updated, skipped = load_routes()
        self.stdout.write(
            self.style.SUCCESS(f"Rutas: {created} creadas, {updated} actualizadas.")
        )
        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"{skipped} rutas omitidas por aeropuertos faltantes. "
                    f"Corre primero: manage.py load_airports"
                )
            )
