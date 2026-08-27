"""Rellena `days_ahead` en el histórico ya acumulado.

El campo se calcula al insertar, así que las ~15.500 filas anteriores quedarían
en `NULL` y no entrarían en el análisis de «cuántos días antes conviene
comprar». El dato ya está en la fila (`flight_date` y `snapshot_at`): esto solo
lo materializa.

Se recorre en lotes con `bulk_update` en vez de una sola sentencia SQL para que
funcione igual en Postgres y en el SQLite de los tests.
"""

from django.db import migrations

LOTE = 2000


def rellenar(apps, schema_editor):
    PriceSnapshot = apps.get_model("flights", "PriceSnapshot")
    pendientes = PriceSnapshot.objects.filter(days_ahead__isnull=True)

    lote = []
    for snap in pendientes.only("id", "flight_date", "snapshot_at").iterator(chunk_size=LOTE):
        # Una fecha de vuelo ya pasada al observarla daria negativo, y el campo
        # es positivo. No deberia ocurrir, pero el barrido puede cruzar la
        # medianoche y no vale la pena romper una migracion por un dia.
        snap.days_ahead = max((snap.flight_date - snap.snapshot_at.date()).days, 0)
        lote.append(snap)
        if len(lote) >= LOTE:
            PriceSnapshot.objects.bulk_update(lote, ["days_ahead"])
            lote.clear()
    if lote:
        PriceSnapshot.objects.bulk_update(lote, ["days_ahead"])


def vaciar(apps, schema_editor):
    """El reverso deja el campo en NULL: el dato se puede recalcular siempre."""
    PriceSnapshot = apps.get_model("flights", "PriceSnapshot")
    PriceSnapshot.objects.update(days_ahead=None)


class Migration(migrations.Migration):

    dependencies = [("flights", "0004_snapshot_days_ahead")]

    operations = [migrations.RunPython(rellenar, vaciar)]
