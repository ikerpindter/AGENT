"""Tests del punto de entrada: run_agent y dotenv simulados, cero red."""

import pytest

from agent import cli
from agent.tools import TOOL_FUNCTIONS


@pytest.fixture
def entorno(monkeypatch):
    """Aisla el CLI: sin .env real, con llave falsa, y captura run_agent."""
    seen = {}

    def fake_run_agent(question, injector=None, tool_functions=None):
        seen.update(question=question, injector=injector, tools=tool_functions)
        return {"final_status": "RECOVERED"}

    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "run_agent", fake_run_agent)
    monkeypatch.setenv("OPENAI_API_KEY", "clave-falsa-de-test")
    monkeypatch.delenv("AGENT_FAULT", raising=False)
    return seen


def test_pregunta_llega_al_loop(entorno):
    cli.main(["¿cuánto es 2 más 2?"])
    assert entorno["question"] == "¿cuánto es 2 más 2?"


def test_backend_table_por_default(entorno):
    cli.main(["hola"])
    assert entorno["tools"] is None  # None = las tools de tabla del loop
    assert entorno["injector"] is None  # fault injection apagada por default


def test_backend_rag_cambia_solo_la_busqueda(entorno):
    cli.main(["hola", "--backend", "rag"])
    tools = entorno["tools"]
    assert set(tools) == set(TOOL_FUNCTIONS)  # mismo contrato de tools
    assert tools["search_financials"] is not TOOL_FUNCTIONS["search_financials"]
    assert tools["calculator"] is TOOL_FUNCTIONS["calculator"]


def test_fault_valido_crea_injector(entorno):
    cli.main(["hola", "--fault", "api_timeout,mode=once"])
    injector = entorno["injector"]
    assert injector is not None
    assert injector.config.kind == "api_timeout"
    assert injector.config.mode == "once"


def test_fault_invalido_truena_legible(entorno):
    with pytest.raises(ValueError, match="Falla desconocida"):
        cli.main(["hola", "--fault", "meteorito"])


def test_sin_llave_sale_con_mensaje_claro(entorno, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["hola"])
    assert "OPENAI_API_KEY" in str(excinfo.value)
    assert ".env.example" in str(excinfo.value)


def test_sin_pregunta_argparse_avisa(entorno):
    with pytest.raises(SystemExit):
        cli.main([])
