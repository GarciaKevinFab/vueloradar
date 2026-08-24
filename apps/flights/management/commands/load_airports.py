from django.core.management.base import BaseCommand

from scripts.load_airports import load_airports


class Command(BaseCommand):
    help = "Carga los aeropuertos monitoreados del Perú en la tabla airports."

    def handle(self, *args, **options):
        created, updated = load_airports()
        self.stdout.write(
            self.style.SUCCESS(f"Aeropuertos: {created} creados, {updated} actualizados.")
        )
