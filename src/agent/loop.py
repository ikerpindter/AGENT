"""El agent loop: el ciclo que conversa con el modelo y ejecuta tools.

Una vuelta del ciclo = una llamada al modelo. Si el modelo pide una tool,
se ejecuta y el resultado se anexa a la conversacion para la siguiente
vuelta. Si el modelo contesta con texto, el ciclo termina. MAX_STEPS es el
tope duro para que nunca se quede girando.
"""

import json
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from agent.tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

MODEL = "gpt-5.4-nano"
MAX_STEPS = 10
MAX_OUTPUT_TOKENS = 1000  # guarda de costo por llamada

# Precios de gpt-5.4-nano en USD por millon de tokens (doc oficial, 2026-07).
PRICE_INPUT_PER_M = 0.20
PRICE_OUTPUT_PER_M = 1.25


def run_agent(question: str, client=None, trace_dir: str | Path = "traces") -> dict:
    """Corre el loop completo para una pregunta y regresa el trace.

    `client` es inyectable para que los tests usen un cliente falso sin red.
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
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cost_usd": 0.0,
    }

    for step in range(1, MAX_STEPS + 1):
        response = client.responses.create(
            model=MODEL,
            input=input_items,
            tools=TOOL_SCHEMAS,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
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
            arguments = json.loads(call.arguments)
            tool_fn = TOOL_FUNCTIONS.get(call.name)
            if tool_fn is None:
                result = f"Error: tool desconocida {call.name!r}"
            else:
                result = tool_fn(**arguments)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result,
                }
            )
            step_record["tool_calls"].append(
                {"name": call.name, "arguments": arguments, "result": result}
            )
        trace["steps"].append(step_record)
        _print_step(step_record)
    else:
        # El for termino sin break: se agotaron los pasos.
        trace["stopped_reason"] = "max_steps"
        print(f"\nALTO: tope de {MAX_STEPS} pasos alcanzado sin respuesta final.")

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


def _save_trace(trace: dict, trace_dir: str | Path) -> None:
    directory = Path(trace_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"run_{stamp}.json"
    path.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Trace guardado en: {path}")
