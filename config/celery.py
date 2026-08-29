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


@app.on_after_finalize.connect
def comprobar_tareas_programadas(sender, **_):
    """Grita si beat programa una tarea que ningún worker sabe ejecutar.

    Es la comprobación que faltaba. Sin ella, el único síntoma de una tarea mal
    registrada es un KeyError perdido entre el resto del log del worker, y
    semanas después un directorio de respaldos vacío.

    Solo avisa; no aborta. Un arranque que se niega a levantar por esto dejaría
    el sitio caído por un problema que solo afecta al trabajo de fondo.
    """
    import logging

    from django.conf import settings

    log = logging.getLogger(__name__)
    programadas = {
        entrada["task"]
        for entrada in getattr(settings, "CELERY_BEAT_SCHEDULE", {}).values()
        if entrada.get("task")
    }
    faltan = sorted(programadas - set(sender.tasks))
    if faltan:
        log.error(
            "Estas tareas están en CELERY_BEAT_SCHEDULE y NO están registradas: "
            "%s. beat las va a despachar y el worker las rechazará con KeyError. "
            "Suele ser que el módulo que las define no lo importa nadie: mira "
            "autodiscover_tasks() en config/celery.py.",
            ", ".join(faltan),
        )


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de humo para verificar que el worker responde."""
    return f"ok desde {self.request.hostname}"
