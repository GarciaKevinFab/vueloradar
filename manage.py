#!/usr/bin/env python
"""Utilidad de línea de comandos de Django para VUELORADAR PERÚ."""
import os
import sys


def _force_utf8_output():
    """La consola de Windows usa cp1252 y revienta con '→' o acentos.

    Todo el proyecto habla español, así que se fuerza UTF-8 en stdout/stderr
    antes de que Django o rich escriban nada.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - stream ya cerrado
                pass


def main():
    _force_utf8_output()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "No se pudo importar Django. ¿Está instalado y activado el entorno virtual?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
