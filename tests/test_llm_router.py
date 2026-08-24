"""Router de IA: cadena de respaldo, circuit breaker y parseo robusto de JSON."""

from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.ai_analyst import llm_router
from apps.ai_analyst.llm_router import LLMResponse, complete, complete_json, parse_json


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def todas_las_keys(settings):
    """Cadena completa configurada, para poder probar los saltos."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    settings.GROQ_API_KEY = "gsk-test"
    settings.DEEPSEEK_API_KEY = "ds-test"
    settings.OLLAMA_BASE_URL = ""


def fake_chain(**comportamientos):
    """Reemplaza la cadena real por callers controlados.

    Cada valor es una excepción a lanzar o el texto a devolver.
    """
    from apps.ai_analyst.llm_router import Provider

    def hacer(nombre, comportamiento):
        def call(system, user, max_tokens):
            if isinstance(comportamiento, Exception):
                raise comportamiento
            return LLMResponse(text=comportamiento, provider=nombre,
                               input_tokens=10, output_tokens=5)
        return call

    cadena = [
        Provider(name=n, call=hacer(n, c), is_configured=lambda: True)
        for n, c in comportamientos.items()
    ]
    return patch("apps.ai_analyst.llm_router.build_chain", return_value=cadena)


# ---------------------------------------------------------------- cadena
@pytest.mark.django_db
def test_responde_el_primero_y_no_consulta_al_resto():
    with fake_chain(anthropic="hola", groq="no deberia llegar"):
        respuesta = complete("sys", "user")

    assert respuesta.provider == "anthropic"
    assert respuesta.text == "hola"


@pytest.mark.django_db
def test_si_claude_esta_caido_responde_groq():
    with fake_chain(anthropic=RuntimeError("503"), groq="respuesta de groq"):
        respuesta = complete("sys", "user")

    assert respuesta.provider == "groq"
    assert respuesta.text == "respuesta de groq"


@pytest.mark.django_db
def test_cae_hasta_el_tercero():
    with fake_chain(
        anthropic=RuntimeError("caido"),
        groq=RuntimeError("caido"),
        deepseek="respuesta de deepseek",
    ):
        respuesta = complete("sys", "user")

    assert respuesta.provider == "deepseek"


@pytest.mark.django_db
def test_todos_caidos_devuelve_none_sin_excepcion():
    with fake_chain(
        anthropic=RuntimeError("x"), groq=RuntimeError("y"), deepseek=RuntimeError("z")
    ):
        assert complete("sys", "user") is None


@pytest.mark.django_db
def test_proveedor_sin_key_se_saltea(settings):
    settings.ANTHROPIC_API_KEY = ""
    settings.GROQ_API_KEY = ""
    settings.DEEPSEEK_API_KEY = ""
    settings.OLLAMA_BASE_URL = ""

    # Cadena real, todas las keys vacías: nadie se intenta siquiera.
    assert complete("sys", "user") is None


@pytest.mark.django_db
def test_ollama_solo_si_hay_base_url(settings):
    settings.OLLAMA_BASE_URL = ""
    nombres = [p.name for p in llm_router.build_chain() if p.is_configured()]
    assert "ollama" not in nombres

    settings.OLLAMA_BASE_URL = "http://localhost:11434"
    nombres = [p.name for p in llm_router.build_chain() if p.is_configured()]
    assert "ollama" in nombres


# -------------------------------------------------------- circuit breaker
@pytest.mark.django_db
def test_tres_fallos_abren_el_circuito(settings):
    settings.LLM_CIRCUIT_FAILURES = 3

    with fake_chain(anthropic=RuntimeError("caido"), groq="ok"):
        for intento in range(1, 4):
            complete("sys", "user")
            if intento < 3:
                assert not llm_router.is_open("anthropic"), f"abierto muy pronto ({intento})"

    assert llm_router.is_open("anthropic") is True


@pytest.mark.django_db
def test_con_el_circuito_abierto_ni_se_intenta(settings):
    settings.LLM_CIRCUIT_FAILURES = 3
    llm_router.record_failure("anthropic")
    llm_router.record_failure("anthropic")
    llm_router.record_failure("anthropic")
    assert llm_router.is_open("anthropic")

    intentos = []

    from apps.ai_analyst.llm_router import Provider

    def anthropic_call(system, user, max_tokens):
        intentos.append("anthropic")
        raise RuntimeError("no deberia llamarse")

    def groq_call(system, user, max_tokens):
        return LLMResponse(text="ok", provider="groq")

    cadena = [
        Provider(name="anthropic", call=anthropic_call, is_configured=lambda: True),
        Provider(name="groq", call=groq_call, is_configured=lambda: True),
    ]
    with patch("apps.ai_analyst.llm_router.build_chain", return_value=cadena):
        respuesta = complete("sys", "user")

    assert intentos == [], "el proveedor con circuito abierto no se llama"
    assert respuesta.provider == "groq"


@pytest.mark.django_db
def test_un_exito_cierra_el_circuito(settings):
    settings.LLM_CIRCUIT_FAILURES = 3
    llm_router.record_failure("anthropic")
    llm_router.record_failure("anthropic")

    with fake_chain(anthropic="ok"):
        complete("sys", "user")

    llm_router.record_failure("anthropic")
    assert llm_router.is_open("anthropic") is False, "el contador se reinició con el éxito"


# --------------------------------------------------------- parseo de JSON
@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ('{"action": "comprar"}', {"action": "comprar"}),
        ('  {"action": "comprar"}  ', {"action": "comprar"}),
        ('```json\n{"action": "comprar"}\n```', {"action": "comprar"}),
        ('```\n{"action": "comprar"}\n```', {"action": "comprar"}),
        ('Aquí está el resultado:\n{"action": "comprar"}', {"action": "comprar"}),
        ('{"action": "comprar"}\n\nEspero que ayude.', {"action": "comprar"}),
    ],
)
def test_parse_json_tolera_respuestas_sucias(crudo, esperado):
    assert parse_json(crudo) == esperado


@pytest.mark.parametrize("crudo", ["", "   ", "no hay json acá", "[1, 2, 3]", "{roto"])
def test_parse_json_devuelve_none_si_no_hay_objeto(crudo):
    assert parse_json(crudo) is None


@pytest.mark.django_db
def test_json_inutilizable_cuenta_como_fallo_y_cae_al_siguiente():
    """Los modelos de respaldo fallan más en formato que en contenido."""
    with fake_chain(anthropic="lo siento, no puedo", groq='{"action": "comprar"}'):
        datos, proveedor = complete_json("sys", "user")

    assert proveedor == "groq"
    assert datos == {"action": "comprar"}


@pytest.mark.django_db
def test_complete_json_devuelve_none_si_nadie_da_json_valido():
    with fake_chain(anthropic="texto suelto", groq="tampoco json"):
        assert complete_json("sys", "user") is None


# ----------------------------------------------------------- contabilidad
@pytest.mark.django_db
def test_registra_el_consumo_del_proveedor_que_respondio():
    from apps.ai_analyst.models import AIUsageLog

    with fake_chain(anthropic=RuntimeError("caido"), groq="ok"):
        complete("sys", "user")

    fallado = AIUsageLog.objects.get(provider="anthropic")
    assert fallado.calls == 1 and fallado.failures == 1

    exitoso = AIUsageLog.objects.get(provider="groq")
    assert exitoso.calls == 1
    assert exitoso.failures == 0
    assert exitoso.input_tokens == 10
    assert exitoso.output_tokens == 5


@pytest.mark.django_db
def test_el_consumo_se_acumula_en_la_misma_fila_del_dia():
    from apps.ai_analyst.models import AIUsageLog

    with fake_chain(anthropic="ok"):
        complete("sys", "user")
        complete("sys", "otro")

    fila = AIUsageLog.objects.get(provider="anthropic")
    assert fila.calls == 2
    assert fila.input_tokens == 20
