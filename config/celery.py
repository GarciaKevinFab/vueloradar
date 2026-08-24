"""Configuración de Celery para VUELORADAR PERÚ.

El scraping vive en su propia cola (`scraping`) con concurrencia baja: el
límite es anti-bloqueo, no performance. Ver CLAUDE.md §7.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("vueloradar")

# Toda la config de Celery vive en settings.py con el prefijo CELERY_.
app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de humo para verificar que el worker responde."""
    return f"ok desde {self.request.hostname}"
