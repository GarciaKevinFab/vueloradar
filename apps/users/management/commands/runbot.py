"""Entrypoint del bot en desarrollo.

    python manage.py runbot
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Levanta el bot de Telegram con polling."

    def handle(self, *args, **options):
        from bot.main import main

        self.stdout.write(self.style.SUCCESS("Levantando el bot… (Ctrl+C para cortar)"))
        main()
