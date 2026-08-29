"""Configuración de Celery para VUELORADAR PERÚ.

El scraping vive en su propia cola (`scraping`) con concurrencia baja: el
límite es anti-bloqueo, no performance. Ver CLAUDE.md §7.
"""

import os

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("vueloradar")

# Toda la config de Celery vive en settings.py con el prefijo CELERY_.
app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()

# `autodiscover_tasks()` sin argumentos SOLO mira `tasks.py` de cada app. Las
# tareas de mantenimiento viven en `apps/scraping/maintenance.py`, así que
# nunca llegaban a registrarse.
#
# EL FALLO ERA MUDO, Y ESO ES LO QUE LO HIZO DURAR
#
#   beat despachaba `backup_database` y `system_healthcheck` sin problema:
#   solo lee el horario, no necesita importar la tarea. El worker las recibía
#   y respondía `KeyError: 'apps.scraping.maintenance.system_healthcheck'`,
#   que no se parece en nada a "el respaldo no corrió".
#
#   Resultado: /backups tenía UN archivo, del día que se probó la restauración
#   a mano. El respaldo diario no se había ejecutado nunca. Y el healthcheck
#   tampoco, o sea que el vigilante que debía avisar de esto era la otra tarea
#   muerta.
app.autodiscover_tasks(related_name="maintenance")


@worker_ready.connect
def comprobar_tareas_programadas(sender=None, **_):
    """Grita si beat programa una tarea que este worker no sabe ejecutar.

    Es la comprobación que faltaba. Sin ella, el único síntoma de una tarea mal
    registrada es un KeyError perdido entre el resto del log del worker, y
    semanas después un directorio de respaldos vacío.

    POR QUE `worker_ready` Y NO `on_after_finalize`

      La primera version usaba `on_after_finalize`, y se disparaba en TODO
      proceso que instancia la app de Celery: tambien en `manage.py shell` y en
      gunicorn, donde el registro de tareas esta a medias porque ese proceso no
      es un worker y no necesita cargarlas.

      Resultado: avisaba de tres tareas "sin registrar" que si lo estaban. Un
      aviso que grita sin motivo se aprende a ignorar, y entonces no sirve
      justo el dia que tiene razon -- que era el problema original.

      `worker_ready` se emite una sola vez, dentro del worker, cuando ya cargo
      todo lo que sabe hacer. Es el unico sitio donde la comparacion significa
      algo.

    Solo avisa; no aborta. Un worker que se niega a arrancar por esto deja el
    trabajo de fondo entero parado por un problema que puede afectar a una sola
    tarea.
    """
    import logging

    from django.conf import settings

    log = logging.getLogger(__name__)
    registradas = set(sender.app.tasks) if sender is not None else set(app.tasks)
    programadas = {
        entrada["task"]
        for entrada in getattr(settings, "CELERY_BEAT_SCHEDULE", {}).values()
        if entrada.get("task")
    }
    faltan = sorted(programadas - registradas)
    if faltan:
        log.error(
            "Estas tareas están en CELERY_BEAT_SCHEDULE y NO están registradas "
            "en este worker: %s. beat las va a despachar y el worker las "
            "rechazará con KeyError. Suele ser que el módulo que las define no "
            "lo importa nadie: mira autodiscover_tasks() en config/celery.py.",
            ", ".join(faltan),
        )
    else:
        log.info("Las %d tareas programadas están registradas.", len(programadas))


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de humo para verificar que el worker responde."""
    return f"ok desde {self.request.hostname}"
