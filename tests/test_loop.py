"""Tests del agent loop con un cliente falso (mock): cero llamadas a la API.

El mock responde con un guion fijo. Cada elemento del guion es lo que "el
modelo" contesta en esa vuelta del ciclo.
"""

import json
from types import SimpleNamespace

from agent.loop import MAX_STEPS, run_agent


def _usage(input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _answer_response(text):
    """Respuesta falsa donde el modelo contesta con texto, sin tools."""
    return SimpleNamespace(
        output=[SimpleNamespace(type="message")],
        output_text=text,
        usage=_usage(),
    )


def _tool_call_response(name, arguments, call_id="call_1"):
    """Respuesta falsa donde el modelo pide una tool."""
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                name=name,
                arguments=json.dumps(arguments),
                call_id=call_id,
            )
        ],
        output_text="",
        usage=_usage(),
    )


class _FakeResponses:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.requests.append(kwargs)
        return self._owner.script.pop(0)


class FakeClient:
    """Cliente falso con la misma forma que OpenAI(): client.responses.create."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.responses = _FakeResponses(self)


def test_sale_cuando_el_modelo_contesta_directo(tmp_path):
    client = FakeClient([_answer_response("Paris")])

    trace = run_agent("capital de Francia", client=client, trace_dir=tmp_path)

    assert trace["final_answer"] == "Paris"
    assert trace["stopped_reason"] == "answer"
    assert len(trace["steps"]) == 1
    assert len(client.requests) == 1


def test_corre_la_tool_y_le_regresa_el_resultado_al_modelo(tmp_path):
    client = FakeClient(
        [
            _tool_call_response(
                "calculator", {"operation": "multiply", "a": 847, "b": 293}
            ),
            _answer_response("El resultado es 248171"),
        ]
    )

    trace = run_agent("cuanto es 847 por 293", client=client, trace_dir=tmp_path)

    # La tool se ejecuto con los argumentos del modelo y dio el resultado real.
    assert trace["steps"][0]["tool_calls"] == [
        {
            "name": "calculator",
            "arguments": {"operation": "multiply", "a": 847, "b": 293},
            "result": "248171",
        }
    ]
    # Y ese resultado si viajo de regreso al modelo en la segunda llamada.
    second_input = client.requests[1]["input"]
    outputs = [
        item
        for item in second_input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert outputs == [
        {"type": "function_call_output", "call_id": "call_1", "output": "248171"}
    ]
    assert trace["final_answer"] == "El resultado es 248171"
    assert trace["stopped_reason"] == "answer"


def test_para_en_max_steps_si_el_modelo_nunca_termina(tmp_path):
    script = [
        _tool_call_response(
            "calculator",
            {"operation": "add", "a": 1, "b": 1},
            call_id=f"call_{i}",
        )
        for i in range(MAX_STEPS)
    ]
    client = FakeClient(script)

    trace = run_agent("suma sin parar", client=client, trace_dir=tmp_path)

    assert trace["stopped_reason"] == "max_steps"
    assert trace["final_answer"] is None
    assert len(trace["steps"]) == MAX_STEPS
    assert len(client.requests) == MAX_STEPS  # ni una llamada mas del tope


def test_encadena_dos_tools_distintas(tmp_path):
    client = FakeClient(
        [
            _tool_call_response(
                "search_financials",
                {"company": "Lennar", "year": 2024, "metric": "total_revenues"},
                call_id="call_a",
            ),
            _tool_call_response(
                "calculator",
                {"operation": "divide", "a": 35441.5, "b": 2},
                call_id="call_b",
            ),
            _answer_response("La mitad es 17720.75"),
        ]
    )

    trace = run_agent("pregunta financiera", client=client, trace_dir=tmp_path)

    # Paso 1: eligio la busqueda y esta dio la cifra real de la tabla.
    assert trace["steps"][0]["tool_calls"][0]["name"] == "search_financials"
    assert trace["steps"][0]["tool_calls"][0]["result"].startswith("35441.5")
    # Paso 2: eligio la calculadora y esta calculo bien.
    assert trace["steps"][1]["tool_calls"][0]["name"] == "calculator"
    assert trace["steps"][1]["tool_calls"][0]["result"] == "17720.75"
    assert trace["stopped_reason"] == "answer"


def test_tool_que_truena_se_vuelve_error_legible_y_el_modelo_recupera(tmp_path):
    from agent.faults import FaultInjector, parse_fault

    injector = FaultInjector(
        parse_fault("tool_exception,tool=search_financials,mode=once")
    )
    client = FakeClient(
        [
            _tool_call_response(
                "search_financials",
                {"company": "Lennar", "year": 2024, "metric": "total_revenues"},
                call_id="call_1",
            ),
            _tool_call_response(
                "search_financials",
                {"company": "Lennar", "year": 2024, "metric": "total_revenues"},
                call_id="call_2",
            ),
            _answer_response("Los ingresos totales fueron 35,441.5 millones."),
        ]
    )

    trace = run_agent(
        "ingresos de Lennar 2024", client=client, trace_dir=tmp_path,
        injector=injector,
    )

    # Paso 1: la falla se convirtio en error legible, sin crash del loop.
    assert trace["steps"][0]["tool_calls"][0]["result"].startswith("Error:")
    # Y el error viajo al modelo como function_call_output normal.
    second_input = client.requests[1]["input"]
    outputs = [i for i in second_input
               if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert outputs[0]["output"].startswith("Error:")
    # Paso 2: el reintento del MODELO (no backoff) funciono.
    assert trace["steps"][1]["tool_calls"][0]["result"].startswith("35441.5")
    assert trace["faults_injected"] == [
        {"kind": "tool_exception", "step": 1, "tool": "search_financials"}
    ]
    assert trace["retries"] == []  # cero reintentos de API: esto fue recovery
    assert trace["final_status"] == "RECOVERED"


def test_api_timeout_se_reintenta_con_backoff(tmp_path):
    from agent.faults import FaultInjector, parse_fault

    injector = FaultInjector(parse_fault("api_timeout,mode=once"))
    client = FakeClient([_answer_response("Paris")])
    waits = []

    trace = run_agent(
        "capital de Francia", client=client, trace_dir=tmp_path,
        injector=injector, retry_sleep=waits.append,
    )

    assert trace["final_answer"] == "Paris"
    assert len(trace["retries"]) == 1
    assert trace["retries"][0]["error_type"] == "APITimeoutError"
    assert waits == [1]  # espero 1s (falso) antes del reintento que funciono
    assert trace["final_status"] == "RECOVERED"


def test_guarda_el_trace_como_json(tmp_path):
    client = FakeClient([_answer_response("hola")])

    run_agent("saluda", client=client, trace_dir=tmp_path)

    files = list(tmp_path.glob("run_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["final_answer"] == "hola"
    assert data["total_input_tokens"] == 10
    assert data["total_output_tokens"] == 5
