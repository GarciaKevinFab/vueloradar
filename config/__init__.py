"""Paquete de configuración del proyecto.

Importar `celery_app` aquí garantiza que la app de Celery quede registrada
cuando Django arranca, de modo que el decorador @shared_task la encuentre.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
