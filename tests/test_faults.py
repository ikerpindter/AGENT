"""Tests del inyector de fallas: configuracion, modos y disparos."""

import openai
import pytest

from agent.faults import GARBAGE, FaultInjector, parse_fault


def test_parse_completo():
    config = parse_fault("tool_exception,tool=search_financials,step=2,mode=once")
    assert config.kind == "tool_exception"
    assert config.tool == "search_financials"
    assert config.step == 2
    assert config.mode == "once"


def test_parse_rechaza_tipos_desconocidos():
    with pytest.raises(ValueError):
        parse_fault("meteorito")


def test_modo_once_dispara_una_sola_vez():
    injector = FaultInjector(parse_fault("tool_exception,mode=once"))

    with pytest.raises(RuntimeError):
        injector.before_tool_run(1, "search_financials")
    # Segunda vez: ya no truena.
    injector.before_tool_run(2, "search_financials")

    assert len(injector.fired) == 1


def test_modo_always_dispara_siempre():
    injector = FaultInjector(parse_fault("tool_exception,mode=always"))

    for step in (1, 2, 3):
        with pytest.raises(RuntimeError):
            injector.before_tool_run(step, "search_financials")

    assert len(injector.fired) == 3


def test_solo_afecta_la_tool_configurada():
    injector = FaultInjector(parse_fault("tool_garbage,tool=search_financials"))

    assert injector.corrupt_result(1, "calculator", "42") == "42"
    assert injector.corrupt_result(1, "search_financials", "35441.5") == GARBAGE
    assert "0123456789" == "".join(c for c in "0123456789" if c not in GARBAGE), (
        "la basura no debe contener digitos"
    )


def test_api_timeout_lanza_error_transitorio():
    injector = FaultInjector(parse_fault("api_timeout,mode=once"))

    with pytest.raises(openai.APITimeoutError):
        injector.before_api_call(1)
    injector.before_api_call(2)  # once: ya no truena


def test_unknown_tool_reescribe_el_nombre():
    injector = FaultInjector(parse_fault("unknown_tool,mode=once"))

    assert injector.rewrite_tool_name(1, "search_financials") == "tool_fantasma"
    assert injector.rewrite_tool_name(2, "search_financials") == "search_financials"
