"""El agent loop: el ciclo que conversa con el modelo y ejecuta tools.

Una vuelta del ciclo = una llamada al modelo. Si el modelo pide una tool,
se ejecuta y el resultado se anexa a la conversacion para la siguiente
vuelta. Si el modelo contesta con texto, el ciclo termina. MAX_STEPS es el
tope duro para que nunca se quede girando.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import openai
from openai import OpenAI

from agent.retry import call_with_retries
from agent.tools import TOOL_FUNCTIONS, TOOL_SCHEMAS
from agent.verify import classify

MODEL = "gpt-5.4-nano"
MAX_STEPS = 10
MAX_OUTPUT_TOKENS = 1000  # guarda de costo por llamada

# Precios de gpt-5.4-nano en USD por millon de tokens (doc oficial, 2026-07).
PRICE_INPUT_PER_M = 0.20
PRICE_OUTPUT_PER_M = 1.25


def run_agent(
    question: str,
    client=None,
    trace_dir: str | Path = "traces",
    injector=None,
    retry_sleep=None,
) -> dict:
    """Corre el loop completo para una pregunta y regresa el trace.

    `client` es inyectable para que los tests usen un cliente falso sin red.
    `injector` (FaultInjector) inyecta fallas a proposito; None = apagado.
    `retry_sleep` reemplaza la espera del backoff en tests.
    """
    if client is None:
        client = OpenAI()

    input_items = [{"role": "user", "content": question}]
    trace = {
        "question": question,
        "model": MODEL,
        "steps": [],
        "final_answer": None,
        "stopped_reason": None,
        "faults_injected": [],
        "retries": [],
        "final_status": None,
        "unverified_numbers": [],
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cost_usd": 0.0,
    }

    for step in range(1, MAX_STEPS + 1):

        def _api_call():
            if injector is not None:
                injector.before_api_call(step)
            return client.responses.create(
                model=MODEL,
                input=input_items,
                tools=TOOL_SCHEMAS,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )

        def _on_retry(attempt, exc, wait):
            trace["retries"].append(
                {
                    "step": step,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "wait_seconds": wait,
                }
            )
            print(
                f"  RETRY: intento {attempt} fallo con {type(exc).__name__}; "
                f"esperando {wait}s antes de reintentar"
            )

        try:
            response = call_with_retries(
                _api_call, on_retry=_on_retry, sleep=retry_sleep or time.sleep
            )
        except openai.APIError as exc:
            # Se agotaron los reintentos (o el error no era reintentable).
            trace["stopped_reason"] = "api_error"
            print(f"\nALTO: la API fallo sin remedio: {type(exc).__name__}: {exc}")
            break
        step_record = {
            "step": step,
            "tool_calls": [],
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        trace["total_input_tokens"] += response.usage.input_tokens
        trace["total_output_tokens"] += response.usage.output_tokens

        function_calls = [
            item for item in response.output if item.type == "function_call"
        ]

        if not function_calls:
            # El modelo ya contesto con texto: fin del ciclo.
            trace["final_answer"] = response.output_text
            trace["stopped_reason"] = "answer"
            step_record["answer"] = response.output_text
            trace["steps"].append(step_record)
            _print_step(step_record)
            break

        # El modelo pidio tools: su salida entra a la conversacion, se
        # ejecuta cada tool y el resultado se anexa con su call_id.
        input_items += response.output
        for call in function_calls:
            name = call.name
            if injector is not None:
                name = injector.rewrite_tool_name(step, name)
            arguments = {}
            try:
                arguments = json.loads(call.arguments)
                tool_fn = TOOL_FUNCTIONS.get(name)
                if tool_fn is None:
                    result = (
                        f"Error: tool desconocida {name!r}. "
                        f"Tools disponibles: {sorted(TOOL_FUNCTIONS)}"
                    )
                else:
                    if injector is not None:
                        injector.before_tool_run(step, name)
                    result = tool_fn(**arguments)
            except Exception as exc:
                # Recovery, no retry: una tool que truena es un error
                # determinista. Se convierte en texto legible y el MODELO
                # decide como seguir; aqui no se arregla nada por atras.
                result = f"Error: la tool {name} fallo: {type(exc).__name__}: {exc}"
            if injector is not None:
                result = injector.corrupt_result(step, name, result)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result,
                }
            )
            step_record["tool_calls"].append(
                {"name": name, "arguments": arguments, "result": result}
            )
        trace["steps"].append(step_record)
        _print_step(step_record)
    else:
        # El for termino sin break: se agotaron los pasos.
        trace["stopped_reason"] = "max_steps"
        print(f"\nALTO: tope de {MAX_STEPS} pasos alcanzado sin respuesta final.")

    if injector is not None:
        trace["faults_injected"] = injector.fired

    verdict = classify(trace)
    trace["final_status"] = verdict["status"]
    trace["unverified_numbers"] = verdict["unverified_numbers"]

    trace["cost_usd"] = round(
        trace["total_input_tokens"] * PRICE_INPUT_PER_M / 1_000_000
        + trace["total_output_tokens"] * PRICE_OUTPUT_PER_M / 1_000_000,
        6,
    )
    _print_totals(trace)
    _save_trace(trace, trace_dir)
    return trace


def _print_step(step_record: dict) -> None:
    print(f"\n--- Paso {step_record['step']} ---")
    for call in step_record["tool_calls"]:
        print(f"  Tool pedida: {call['name']}")
        print(f"  Argumentos:  {call['arguments']}")
        print(f"  Resultado:   {call['result']}")
    if "answer" in step_record:
        print(f"  Respuesta final: {step_record['answer']}")
    print(
        f"  Tokens: {step_record['input_tokens']} entrada / "
        f"{step_record['output_tokens']} salida"
    )


def _print_totals(trace: dict) -> None:
    print(
        f"\nTotales: {trace['total_input_tokens']} tokens de entrada, "
        f"{trace['total_output_tokens']} de salida. "
        f"Costo estimado: ${trace['cost_usd']:.6f} USD"
    )
    print(
        f"Fallas inyectadas: {len(trace['faults_injected'])} | "
        f"Reintentos de API: {len(trace['retries'])}"
    )
    status_line = f"ESTADO FINAL: {trace['final_status']}"
    if trace["unverified_numbers"]:
        status_line += f"  (numeros sin pedigri: {trace['unverified_numbers']})"
    print(status_line)


def _save_trace(trace: dict, trace_dir: str | Path) -> None:
    directory = Path(trace_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"run_{stamp}.json"
    path.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Trace guardado en: {path}")
