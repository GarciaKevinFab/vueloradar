"""Copia del backup fuera del servidor, en R2 de Cloudflare.

En Railway el filesystem es efímero: se borra en cada deploy. Un `pg_dump`
que queda en disco local ahí no es un backup, es un archivo temporal con
nombre serio. Por eso el dump se sube apenas se genera.

R2 y no otro: es S3-compatible, el egress no se cobra y ya vas a tener cuenta
de Cloudflare por el dominio y el CDN. Sin las credenciales configuradas la
subida no ocurre y se dice en el log — en un VPS con volumen real el archivo
local alcanza, y esto no debe romper el backup.

**Cuando R2 sí está configurado, un fallo de subida es grave**: significa que
no queda ninguna copia. Por eso `backup_database` avisa al admin en ese caso,
en vez de anotarlo y seguir.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """¿Hay credenciales de R2 completas?"""
    return all([
        getattr(settings, "R2_ACCOUNT_ID", ""),
        getattr(settings, "R2_ACCESS_KEY_ID", ""),
        getattr(settings, "R2_SECRET_ACCESS_KEY", ""),
        getattr(settings, "R2_BUCKET", ""),
    ])


def upload_backup(archivo: Path) -> bool:
    """Sube el dump a R2. Devuelve si quedó guardado.

    Nunca lanza: un fallo se reporta con False para que el caller decida qué
    hacer. Acá no se puede tirar abajo el barrido.
    """
    if not is_configured():
        logger.info("offsite: R2 sin configurar, el backup queda solo en disco")
        return False

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        logger.error("offsite: falta boto3; el backup no sale del servidor")
        return False

    endpoint = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    clave = f"{settings.R2_PREFIX}{archivo.name}" if settings.R2_PREFIX else archivo.name

    try:
        cliente = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        cliente.upload_file(str(archivo), settings.R2_BUCKET, clave)
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.error("offsite: fallo al subir %s a R2: %s", archivo.name, exc)
        return False

    logger.info("offsite: %s subido a r2://%s/%s", archivo.name, settings.R2_BUCKET, clave)
    return True
