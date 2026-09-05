"""El sitio y el bot hablan en español peruano, no en rioplatense.

No es una preferencia de estilo: el usuario final es peruano y el voseo suena
extranjero, que es justo lo contrario de lo que un sitio de vuelos domésticos
necesita transmitir. El copy se corrigió en dos tandas —la web primero, el bot
y el prompt de la IA después— y entre una y otra pasó una semana en la que
nadie se dio cuenta de que faltaba la mitad. Este test existe para que no haya
una tercera tanda.

Cubre el prompt del `nl_parser` a propósito: si las instrucciones al modelo
están en voseo, el modelo responde en voseo, y ahí no hay plantilla que valga.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

#: Dónde vive el texto que lee una persona: plantillas, mensajes del bot,
#: correos y los prompts que gobiernan lo que contesta la IA.
AMBITOS = ("apps", "bot", "templates")

#: Formas voseantes. Los imperativos son los que más se cuelan, porque en
#: tuteo muchos son irregulares (`poné`→`pon`, `hacé`→`haz`, `decí`→`di`) y
#: quitar la tilde no alcanza.
VOSEO = re.compile(
    r"\b("
    r"vos|sos|querés|podés|tenés|sabés|venís|salís|vivís|decís|elegís|seguís|"
    r"preferís|usás|volvés|andás|mirás|hacés|ponés|buscás|dejás|comprás|pagás|"
    r"viajás|volás|navegás|necesitás|escribís|pedís|quedás|recibís|recordás|"
    r"revisás|devolvés|"
    r"andá|mirá|dejá|buscá|elegí|comprá|poné|hacé|decí|tocá|esperá|mandá|"
    r"revisá|volvé|sumá|probá|entrá|vení|abrí|cerrá|guardá|pensá|creá|"
    r"descartá|devolvé|resolvé|respondé|confirmá|activá|desactivá|usá|"
    r"fijate|quedate|llevate|acordate|sumale|contame|decime|avisame|mandale"
    r")\b",
    re.IGNORECASE,
)


def _archivos_de_texto():
    for ambito in AMBITOS:
        base = RAIZ / ambito
        if not base.exists():
            continue
        for patron in ("**/*.html", "**/*.py"):
            for ruta in base.glob(patron):
                if "migrations" in ruta.parts or "__pycache__" in ruta.parts:
                    continue
                yield ruta


def test_no_queda_voseo_en_el_texto_que_lee_el_usuario():
    hallazgos = []
    revisados = 0
    for ruta in _archivos_de_texto():
        revisados += 1
        for numero, linea in enumerate(
            ruta.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for encontrado in VOSEO.finditer(linea):
                hallazgos.append(
                    f"{ruta.relative_to(RAIZ)}:{numero}: «{encontrado.group(0)}» "
                    f"— {linea.strip()[:80]}"
                )

    # Si el barrido no encuentra archivos —una carpeta renombrada, un glob mal
    # escrito— la lista sale vacía y el test pasa sin haber mirado nada. Es la
    # forma más silenciosa de perder una comprobación: se queda en verde.
    assert revisados > 50, f"el barrido solo vio {revisados} archivos: no está mirando el proyecto"
    assert not hallazgos, "Voseo encontrado:\n" + "\n".join(hallazgos)


@pytest.mark.parametrize(
    "frase, esperado",
    [
        ("Poné el par en el destino", True),
        ("Pon el par en el destino", False),
        ("No encontré vuelos", False),      # 1ª persona, no es voseo
        ("Ya tenías esa alerta, la dejé activa", False),
        ("El caché de la zona", False),     # sustantivo con tilde
    ],
)
def test_el_detector_no_marca_lo_que_es_correcto(frase, esperado):
    """Sin esto el test de arriba podría estar pasando por no detectar nada.

    Los falsos positivos importan tanto como los negativos: «no encontré» y
    «la dejé activa» son primera persona y son correctos; marcarlos obligaría
    a reescribir copy que está bien.
    """
    assert bool(VOSEO.search(frase)) is esperado
