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
    r"fijate|quedate|llevate|acordate|sumale|contame|decime|avisame|mandale|"
    r"notificame|preguntale|decile|contale|escribile|mostrame|pedile|ponele"
    r")\b",
    re.IGNORECASE,
)

#: Segunda red, estructural. La lista de arriba nunca va a estar completa: se
#: escribió creyéndola exhaustiva y aun así dejó pasar «Pregúntale al bot» y
#: «Notifícame cuando baje» —los dos CTA más visibles del sitio, uno en todas
#: las páginas y el otro en 58.
#:
#: El imperativo voseante con pronombre pegado se reconoce por la forma: en
#: tuteo la palabra sería esdrújula y llevaría tilde (`notifícame`), y en
#: voseo no la lleva (`notificame`). Así que cualquier palabra larga sin
#: tilde terminada en pronombre es sospechosa, la conozca yo o no.
ENCLITICO = re.compile(r"\b[a-z]{6,}(?:ame|ale|alo|ala|ile|ilo|arme|arte)\b")

#: Palabras que la regla de arriba marca y son correctas. Se declaran una a una
#: a propósito: si aparece una nueva, el test falla y obliga a mirarla en vez
#: de ensanchar la regla hasta que no detecte nada.
ENCLITICO_LEGITIMO = {
    # Infinitivo + pronombre: correcto en cualquier variante del español.
    "identificarte",
    # Término técnico, y encima en inglés.
    "percentile",
    # Palabras llanas que la regla marca por su terminación.
    "instale", "peruanos", "username", "señale", "detalle", "traslado",
    "regalo", "intervalo", "escala", "señala", "sigilo", "estilo",
}


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


def test_no_hay_encliticos_voseantes_sin_declarar():
    """Cubre lo que la lista explícita no conoce, que es donde falló.

    La lista de formas se escribió creyéndola completa y aun así dejó pasar
    «Pregúntale al bot» y «Notifícame cuando baje». Esta regla no depende de
    conocer la palabra: marca cualquier candidato por su forma y obliga a
    declararlo correcto una vez. Un hallazgo nuevo es una decisión, no un fallo
    automático — pero tiene que ser una decisión de alguien.
    """
    sospechosos = {}
    for ruta in _archivos_de_texto():
        for numero, linea in enumerate(
            ruta.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for encontrado in ENCLITICO.finditer(linea.lower()):
                palabra = encontrado.group(0)
                if palabra in ENCLITICO_LEGITIMO:
                    continue
                sospechosos.setdefault(palabra, []).append(
                    f"{ruta.relative_to(RAIZ)}:{numero}"
                )

    assert not sospechosos, (
        "Posible imperativo voseante con pronombre pegado. Si es tuteo correcto, "
        "agrégalo a ENCLITICO_LEGITIMO; si no, ponle la tilde:\n"
        + "\n".join(f"  {w}: {', '.join(d)}" for w, d in sorted(sospechosos.items()))
    )
