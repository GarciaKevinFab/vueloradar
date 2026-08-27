"""Backup fuera del servidor. En Railway el disco es efímero."""

from pathlib import Path

import pytest

from apps.scraping import offsite


@pytest.fixture
def r2(settings, tmp_path):
    settings.R2_ACCOUNT_ID = "cuenta"
    settings.R2_ACCESS_KEY_ID = "clave"
    settings.R2_SECRET_ACCESS_KEY = "secreto"
    settings.R2_BUCKET = "vueloradar"
    settings.R2_PREFIX = "backups/"
    archivo = tmp_path / "vueloradar-20260827-030000.dump"
    archivo.write_bytes(b"dump")
    return archivo


def test_sin_credenciales_no_sube(settings, tmp_path):
    settings.R2_ACCOUNT_ID = ""
    assert offsite.is_configured() is False
    assert offsite.upload_backup(tmp_path / "x.dump") is False


def test_credenciales_incompletas_cuentan_como_sin_configurar(settings, r2):
    settings.R2_SECRET_ACCESS_KEY = ""
    assert offsite.is_configured() is False


def test_sube_al_bucket_con_el_prefijo(settings, r2, monkeypatch):
    llamadas = {}

    class _Cliente:
        def upload_file(self, ruta, bucket, clave):
            llamadas.update(ruta=ruta, bucket=bucket, clave=clave)

    def _client(servicio, **kwargs):
        llamadas["endpoint"] = kwargs["endpoint_url"]
        return _Cliente()

    import boto3

    monkeypatch.setattr(boto3, "client", _client)
    assert offsite.upload_backup(r2) is True
    assert llamadas["bucket"] == "vueloradar"
    assert llamadas["clave"] == "backups/vueloradar-20260827-030000.dump"
    assert llamadas["endpoint"] == "https://cuenta.r2.cloudflarestorage.com"


def test_un_fallo_de_red_no_revienta(settings, r2, monkeypatch):
    """El backup no puede tumbar el barrido; el caller decide qué hacer."""
    from botocore.exceptions import ClientError

    class _Cliente:
        def upload_file(self, *a):
            raise ClientError({"Error": {"Code": "500"}}, "PutObject")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Cliente())
    assert offsite.upload_backup(r2) is False
