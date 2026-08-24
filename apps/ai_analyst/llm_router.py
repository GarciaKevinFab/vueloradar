"""Router de LLMs con cadena de respaldo.

Intenta los proveedores en orden hasta que uno responda. Si todos fallan
devuelve `None` y el sistema sigue sin veredicto: una alerta de precio nunca
se bloquea porque la IA esté caída.

Groq, DeepSeek y Ollama son OpenAI-compatible, así que comparten cliente y
código; solo cambian `base_url`, modelo y key.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable

from django.conf import settings
from django.core.cache import cache

from . import usage

logger = logging.getLogger(__name__)

ANTHROPIC = "anthropic"
GROQ = "groq"
DEEPSEEK = "deepseek"
OLLAMA = "ollama"


@dataclass(frozen=True)
class LLMResponse:
    """Lo que devolvió el proveedor que sí respondió."""

    text: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Provider:
    """Un eslabón de la cadena."""

    name: str
    call: Callable[[str, str, int], LLMResponse]
    is_configured: Callable[[], bool]


# ------------------------------------------------------------- circuit breaker
def _failures_key(provider: str) -> str:
    return f"llm:{provider}:failures"


def _open_key(provider: str) -> str:
    return f"llm:{provider}:open"


def is_open(provider: str) -> bool:
    """El circuito está abierto: el proveedor se saltea."""
    try:
        return bool(cache.get(_open_key(provider)))
    except Exception:  # noqa: BLE001
        return False


def record_failure(provider: str) -> int:
    """Suma un fallo y abre el circuito si se llegó al umbral."""
    try:
        cache.add(_failures_key(provider), 0, None)
        fallos = int(cache.incr(_failures_key(provider)))
    except Exception:  # noqa: BLE001
        return 0

    if fallos >= settings.LLM_CIRCUIT_FAILURES:
        cache.set(_open_key(provider), "1", settings.LLM_CIRCUIT_OPEN_SECONDS)
        logger.error(
            "llm_router: %s se salta por %d min tras %d fallos seguidos",
            provider, settings.LLM_CIRCUIT_OPEN_SECONDS // 60, fallos,
        )
    return fallos


def record_success(provider: str) -> None:
    """Un éxito cierra el circuito y limpia el historial."""
    try:
        cache.delete(_failures_key(provider))
        cache.delete(_open_key(provider))
    except Exception:  # noqa: BLE001
        pass


def reset_circuit(provider: str) -> None:
    record_success(provider)


# ------------------------------------------------------------------ proveedores
def _call_anthropic(system: str, user: str, max_tokens: int) -> LLMResponse:
    import anthropic

    client = anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY, timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0
    )
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "disabled"},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude rechazó la solicitud")

    texto = "".join(b.text for b in response.content if b.type == "text")
    return LLMResponse(
        text=texto,
        provider=ANTHROPIC,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def _openai_compatible(
    name: str, base_url: str, api_key: str, model: str
) -> Callable[[str, str, int], LLMResponse]:
    """Fabrica el caller de un proveedor con API estilo OpenAI."""

    def call(system: str, user: str, max_tokens: int) -> LLMResponse:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key or "no-key-needed",
            base_url=base_url,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        uso = getattr(response, "usage", None)
        return LLMResponse(
            text=response.choices[0].message.content or "",
            provider=name,
            input_tokens=getattr(uso, "prompt_tokens", 0) or 0,
            output_tokens=getattr(uso, "completion_tokens", 0) or 0,
        )

    return call


def build_chain() -> list[Provider]:
    """La cadena, en orden de preferencia. Se arma en cada llamada porque los
    tests cambian settings y una lista a nivel de módulo los ignoraría."""
    return [
        Provider(
            name=ANTHROPIC,
            call=_call_anthropic,
            is_configured=lambda: bool(settings.ANTHROPIC_API_KEY),
        ),
        Provider(
            name=GROQ,
            call=_openai_compatible(
                GROQ, "https://api.groq.com/openai/v1",
                settings.GROQ_API_KEY, settings.GROQ_MODEL,
            ),
            is_configured=lambda: bool(settings.GROQ_API_KEY),
        ),
        Provider(
            name=DEEPSEEK,
            call=_openai_compatible(
                DEEPSEEK, "https://api.deepseek.com/v1",
                settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_MODEL,
            ),
            is_configured=lambda: bool(settings.DEEPSEEK_API_KEY),
        ),
        Provider(
            name=OLLAMA,
            call=_openai_compatible(
                OLLAMA, f"{settings.OLLAMA_BASE_URL.rstrip('/')}/v1",
                "ollama", settings.OLLAMA_MODEL,
            ),
            # Solo dev local: si no está seteada la URL, ni se intenta.
            is_configured=lambda: bool(settings.OLLAMA_BASE_URL),
        ),
    ]


# ------------------------------------------------------------------ interfaz
def complete(system: str, user: str, max_tokens: int = 400) -> LLMResponse | None:
    """Devuelve la respuesta del primer proveedor que conteste, o `None`."""
    return _run_chain(system, user, max_tokens, validate=None)


def complete_json(system: str, user: str, max_tokens: int = 400) -> tuple[dict, str] | None:
    """Igual que `complete` pero exige JSON válido.

    Un proveedor que devuelve algo no parseable cuenta como fallo y se pasa al
    siguiente: los modelos de respaldo fallan más en formato que en contenido.
    """
    resultado = _run_chain(system, user, max_tokens, validate=parse_json)
    if resultado is None:
        return None
    respuesta, datos = resultado
    return datos, respuesta.provider


def _run_chain(system: str, user: str, max_tokens: int, validate):
    """Recorre la cadena. `validate` puede rechazar una respuesta sintácticamente
    válida pero inutilizable, lo que hace caer al siguiente proveedor."""
    intentados = []

    for provider in build_chain():
        if not provider.is_configured():
            continue
        if is_open(provider.name):
            logger.info("llm_router: %s salteado, circuito abierto", provider.name)
            continue

        intentados.append(provider.name)
        try:
            respuesta = provider.call(system, user, max_tokens)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo cae al siguiente
            logger.warning("llm_router: %s falló (%s)", provider.name, exc)
            record_failure(provider.name)
            usage.record(provider.name, failed=True)
            continue

        if validate is None:
            record_success(provider.name)
            usage.record(
                provider.name,
                input_tokens=respuesta.input_tokens,
                output_tokens=respuesta.output_tokens,
            )
            logger.info("llm_router: respondió %s", provider.name)
            return respuesta

        datos = validate(respuesta.text)
        if datos is None:
            logger.warning("llm_router: %s devolvió JSON inutilizable", provider.name)
            record_failure(provider.name)
            usage.record(provider.name, failed=True)
            continue

        record_success(provider.name)
        usage.record(
            provider.name,
            input_tokens=respuesta.input_tokens,
            output_tokens=respuesta.output_tokens,
        )
        logger.info("llm_router: respondió %s", provider.name)
        return respuesta, datos

    logger.error(
        "llm_router: ningún proveedor respondió (intentados: %s)",
        ", ".join(intentados) or "ninguno configurado",
    )
    return None


# --------------------------------------------------------------- parseo de JSON
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json(text: str) -> dict | None:
    """Saca un objeto JSON de una respuesta sucia.

    Los modelos de respaldo envuelven el JSON en fences, lo preceden de
    "Aquí está el resultado:" o agregan comentarios. Se intenta, en orden:
    parsear tal cual, sacar el contenido del fence, y como último recurso
    agarrar el primer bloque entre llaves.
    """
    if not text:
        return None

    for candidato in _json_candidates(text):
        try:
            datos = json.loads(candidato)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(datos, dict):
            return datos

    return None


def _json_candidates(text: str):
    limpio = text.strip()
    yield limpio

    fence = _FENCE_RE.search(limpio)
    if fence:
        yield fence.group(1)

    objeto = _OBJECT_RE.search(limpio)
    if objeto:
        yield objeto.group(0)
