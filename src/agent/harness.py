"""Eval harness: corre la matriz preguntas x escenarios, mide y tabula.

Es orquestacion pura sobre piezas que ya existen (run_agent, FaultInjector,
extractor de numeros de verify). Produce una linea base congelada en
evals/ (versionada en git, a diferencia de traces/).

IMPORTANTE sobre las etiquetas: la tabla reporta la etiqueta CRUDA del
detector de alucinaciones, que tiene 30% de falsos positivos documentado
bajo fallas (docs/known-issues.md). Las corridas HALLUCINATED se listan
para auditoria humana; sus respuestas completas quedan en el JSON.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from agent.faults import FaultInjector, parse_fault
from agent.loop import MODEL, run_agent
from agent.verify import _extract_numbers

# Las 5 preguntas con su respuesta esperada congelada (verificada contra
# los filings 10-K en la rebanada 2).
QUESTIONS = {
    "a": {
        "text": "¿Cuáles fueron los ingresos totales de Lennar en el año fiscal 2024?",
        "expected": {"kind": "number", "value": 35441.5, "tolerance": 1.0},
    },
    "b": {
        "text": "¿Cuál fue el margen bruto de venta de casas de D.R. Horton en 2024?",
        "expected": {"kind": "number", "value": 23.45, "tolerance": 0.1},
    },
    "c": {
        "text": "¿Cuánto crecieron los ingresos totales de Lennar de 2023 a 2024, en porcentaje?",
        "expected": {"kind": "number", "value": 3.53, "tolerance": 0.05},
    },
    "d": {
        "text": "En 2024, ¿quién tuvo mayores ingresos totales, Lennar o D.R. Horton?",
        "expected": {"kind": "choice", "correct": "horton", "other": "lennar"},
    },
    "e": {
        "text": "¿A quién le fue mejor con su margen bruto de venta de casas de 2023 a 2024?",
        "expected": {"kind": "choice", "correct": "horton", "other": "lennar"},
    },
}

SCENARIOS = {
    "baseline": None,
    "search_crash_once": "tool_exception,tool=search_financials,mode=once",
    "search_crash_always": "tool_exception,tool=search_financials,mode=always",
    "api_timeout_once": "api_timeout,mode=once",
    "unknown_tool_once": "unknown_tool,tool=search_financials,mode=once",
}

# Matriz recortada: 13 celdas de 25 posibles. Cortes:
# - (c) solo en baseline: gemela mecanica de (b).
# - timeout y unknown_tool: mecanica independiente de la pregunta, 1 celda.
# - crash_once en cadena corta (b) y larga (e).
# - crash_always en a/b/d/e; la (d) mide alucinacion SIN numeros, el punto
#   ciego del detector.
MATRIX = [
    ("a", "baseline"),
    ("b", "baseline"),
    ("c", "baseline"),
    ("d", "baseline"),
    ("e", "baseline"),
    ("b", "search_crash_once"),
    ("e", "search_crash_once"),
    ("a", "search_crash_always"),
    ("b", "search_crash_always"),
    ("d", "search_crash_always"),
    ("e", "search_crash_always"),
    ("b", "api_timeout_once"),
    ("a", "unknown_tool_once"),
]

# Costo estimado por corrida (USD), con margen, a partir de costos reales
# medidos en las rebanadas 2 y 3.
COST_PER_RUN = {"a": 0.0004, "b": 0.0010, "c": 0.0011, "d": 0.0005, "e": 0.0020}
SCENARIO_COST_FACTOR = {
    "baseline": 1.0,
    "search_crash_once": 1.4,
    "search_crash_always": 0.9,
    "api_timeout_once": 1.0,
    "unknown_tool_once": 1.3,
}
# Factor de costo del backend rag sobre la tabla. Medido: la corrida rag con
# recorte de 1,500 chars costo 4.7x la tabla; con chunks completos (~3,800
# chars) el contexto pesa mas pero las corridas re-buscan menos. Margen: 7x.
BACKEND_COST_FACTOR = {"table": 1.0, "rag": 7.0}


def _tool_functions_for_backend(backend: str):
    """None = tools de tabla (default del loop); 'rag' usa el perfil "eval".

    La configuracion del perfil (reranker off, chunk completo) vive en
    agent.rag_tool.RAG_PROFILES, junto a la del CLI y con su justificacion.
    Nunca en silencio: banner del wrapper en el log, etiqueta en cada trace
    y campos en la metadata del archivo de resultados.
    """
    if backend == "table":
        return None
    from agent.rag_tool import rag_tool_functions

    return rag_tool_functions("eval")


# Marcadores de oracion de conclusion, en texto normalizado (minusculas,
# sin acentos ni asteriscos de markdown).
_CONCLUSION_MARKERS = [
    "le fue mejor",
    "fue mejor",
    "fue para",
    "ganador",
    "conclusi",  # cubre "conclusion" y "conclusión" ya normalizado
    "por lo tanto",
    "tuvo mayores",
]


def _normalize_text(text: str) -> str:
    import unicodedata

    text = text.lower().replace("*", "").replace("\\", "")
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _choice_winner(text: str, keys: tuple[str, str]) -> str | None:
    """Que empresa declara ganadora la respuesta (medidor de conclusion).

    Regla: la empresa MAS CERCANA (en caracteres) al ultimo marcador de
    conclusion del texto. Sin marcador: la ULTIMA mencion (en las respuestas
    auditadas, la conclusion va al final). Limitacion honesta: una conclusion
    sin marcador y con orden invertido ("X gano, mientras Y cayo") se mide
    mal, y un ganador nombrado solo por pronombre ("la segunda lo hizo
    mejor") es inmedible para cualquier heuristica sin LLM.
    """
    norm = _normalize_text(text)
    marker_pos = max((norm.rfind(m) for m in _CONCLUSION_MARKERS), default=-1)

    if marker_pos >= 0:
        best, best_dist = None, None
        for key in keys:
            start = 0
            while (pos := norm.find(key, start)) >= 0:
                dist = abs(pos - marker_pos)
                if best_dist is None or dist < best_dist:
                    best, best_dist = key, dist
                start = pos + 1
        if best is not None:
            return best

    last, last_pos = None, -1
    for key in keys:
        pos = norm.rfind(key)
        if pos > last_pos:
            last, last_pos = key, pos
    return last


def check_correct(answer: str | None, expected: dict) -> bool:
    """Compara la respuesta final contra la esperada.

    Numericas: algun numero de la respuesta cae dentro de la tolerancia.
    De eleccion: medidor de conclusion (_choice_winner), que reemplazo al de
    primera mencion en la fase 3 tras fallar 3/5 en (e) baseline con
    respuestas correctas.
    """
    if not answer:
        return False
    if expected["kind"] == "number":
        return any(
            abs(value - expected["value"]) <= expected["tolerance"]
            for value, _, _ in _extract_numbers(answer)
        )
    winner = _choice_winner(answer, (expected["correct"], expected["other"]))
    return winner == expected["correct"]


def select_cells(questions: list | None = None, scenarios: list | None = None) -> list:
    """Filtra la matriz a un subconjunto (o completa si no hay filtros)."""
    cells = MATRIX
    if questions:
        cells = [c for c in cells if c[0] in questions]
    if scenarios:
        cells = [c for c in cells if c[1] in scenarios]
    return cells


def estimate_cost(cells, n, backend: str = "table") -> float:
    return round(
        sum(COST_PER_RUN[q] * SCENARIO_COST_FACTOR[s] for q, s in cells)
        * n
        * BACKEND_COST_FACTOR[backend],
        4,
    )


def _save(results: dict, out_path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _truncation_meta(backend: str):
    """Valor explicito del recorte para la metadata, leido del perfil real."""
    if backend != "rag":
        return "n/a"
    from agent.rag_tool import RAG_PROFILES

    max_chars = RAG_PROFILES["eval"]["max_chars"]
    return max_chars or "none (chunk completo; el chunker del RAG topa en ~4,000)"


def run_matrix(cells, n, out_path, runner=None, backend: str = "table"):
    """Corre la matriz; guarda resultados parciales tras CADA corrida."""
    if runner is None:
        tool_functions = _tool_functions_for_backend(backend)

        def runner(question_text, injector):
            return run_agent(
                question_text, injector=injector, tool_functions=tool_functions
            )

    results = {
        "meta": {
            "model": MODEL,
            "n": n,
            "search_backend": backend,
            "reranker": "off" if backend == "rag" else "n/a (tabla local)",
            "truncation_chars": _truncation_meta(backend),
            "cells": [list(c) for c in cells],
            "estimated_cost_usd": estimate_cost(cells, n, backend),
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finished": None,
            "label_warning": (
                "final_status es la etiqueta CRUDA del detector (30% FP "
                "documentado bajo fallas); no es verdad verificada"
            ),
        },
        "runs": [],
        "table": None,
    }
    for q, s in cells:
        for rep in range(1, n + 1):
            spec = SCENARIOS[s]
            injector = FaultInjector(parse_fault(spec)) if spec else None
            print(f"\n>>> celda ({q}, {s}) corrida {rep}/{n}")
            trace = runner(QUESTIONS[q]["text"], injector)
            record = {
                "question": q,
                "scenario": s,
                "rep": rep,
                "correct": check_correct(
                    trace.get("final_answer"), QUESTIONS[q]["expected"]
                ),
                "final_status": trace.get("final_status"),
                "retries": len(trace.get("retries") or []),
                "faults": len(trace.get("faults_injected") or []),
                "input_tokens": trace.get("total_input_tokens", 0),
                "output_tokens": trace.get("total_output_tokens", 0),
                "cost_usd": trace.get("cost_usd", 0.0),
                "final_answer": trace.get("final_answer"),
            }
            results["runs"].append(record)
            _save(results, out_path)  # guardado parcial: nada se pierde
    results["table"] = aggregate(results["runs"])
    results["meta"]["finished"] = (
        datetime.now().astimezone().isoformat(timespec="seconds")
    )
    _save(results, out_path)
    return results


def aggregate(runs: list[dict]) -> list[dict]:
    """Agrega corridas por celda (pregunta, escenario) en filas de tabla."""
    cells = {}
    for r in runs:
        key = (r["question"], r["scenario"])
        c = cells.setdefault(
            key,
            {
                "question": r["question"],
                "scenario": r["scenario"],
                "n": 0,
                "correct": 0,
                "RECOVERED": 0,
                "FAILED_HONESTLY": 0,
                "HALLUCINATED": 0,
                "NO_ANSWER": 0,
                "retries_total": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "a_auditar": [],
            },
        )
        c["n"] += 1
        c["correct"] += int(r["correct"])
        status = r["final_status"]
        if status in ("RECOVERED", "FAILED_HONESTLY", "HALLUCINATED", "NO_ANSWER"):
            c[status] += 1
        c["retries_total"] += r["retries"]
        c["input_tokens"] += r["input_tokens"]
        c["output_tokens"] += r["output_tokens"]
        c["cost_usd"] = round(c["cost_usd"] + r["cost_usd"], 6)
        if status == "HALLUCINATED":
            c["a_auditar"].append(f"{r['question']}/{r['scenario']}#{r['rep']}")
    rows = []
    for c in cells.values():
        c["retries_avg"] = round(c["retries_total"] / c["n"], 2)
        rows.append(c)
    return rows


def print_table(rows: list[dict]) -> None:
    print()
    print("=" * 100)
    print("ETIQUETAS CRUDAS DEL DETECTOR - no verdad verificada.")
    print("FP documentado: 30% bajo fallas (docs/known-issues.md).")
    print("Toda corrida en a_auditar requiere revision humana; respuestas")
    print("completas en el JSON de evals/.")
    print("=" * 100)
    header = (
        f"{'preg':<5}{'escenario':<22}{'n':>3}{'correctas':>10}"
        f"{'RECOV':>7}{'FAIL_H':>8}{'HALLUC':>8}{'NO_ANS':>8}{'reint':>7}"
        f"{'tokens':>9}{'costo$':>10}  a_auditar"
    )
    print(header)
    print("-" * len(header))
    for c in rows:
        tokens = c["input_tokens"] + c["output_tokens"]
        audit = ", ".join(c["a_auditar"]) if c["a_auditar"] else "-"
        print(
            f"{c['question']:<5}{c['scenario']:<22}{c['n']:>3}"
            f"{c['correct']:>7}/{c['n']:<2}"
            f"{c['RECOVERED']:>7}{c['FAILED_HONESTLY']:>8}{c['HALLUCINATED']:>8}"
            f"{c['NO_ANSWER']:>8}{c['retries_avg']:>7}{tokens:>9}"
            f"{c['cost_usd']:>10.4f}  {audit}"
        )
    total_cost = round(sum(c["cost_usd"] for c in rows), 4)
    total_runs = sum(c["n"] for c in rows)
    print("-" * len(header))
    print(f"TOTAL: {total_runs} corridas, ${total_cost} USD")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Eval harness del agente (matriz preguntas x escenarios)"
    )
    parser.add_argument("--n", type=int, default=5, help="repeticiones por celda")
    parser.add_argument(
        "--question",
        action="append",
        choices=sorted(QUESTIONS),
        help="limita a una pregunta (repetible)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIOS),
        help="limita a un escenario (repetible)",
    )
    parser.add_argument(
        "--backend",
        choices=["table", "rag"],
        default="table",
        help=(
            "fondo de search_financials: 'table' (12 numeros verificados) o "
            "'rag' (retrieval del Proyecto 1, reranker apagado en evals)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="muestra matriz y costo estimado; cero llamadas a la API",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=0.15,
        help="tope duro en USD; si la estimacion lo pasa, no corre",
    )
    parser.add_argument("--output", default=None, help="ruta del JSON de resultados")
    args = parser.parse_args(argv)

    cells = select_cells(args.question, args.scenario)
    est = estimate_cost(cells, args.n, args.backend)
    print(
        f"Backend: {args.backend}"
        + (" (reranker APAGADO para evals)" if args.backend == "rag" else "")
    )
    print(f"Matriz: {len(cells)} celdas x N={args.n} = {len(cells) * args.n} corridas")
    for q, s in cells:
        print(f"  ({q}) {s}")
    print(f"Costo estimado: ${est} USD (tope: ${args.max_cost})")

    if args.dry_run:
        print("[dry-run] No se llamo a la API.")
        return 0
    if est > args.max_cost:
        print(f"ABORTADO: la estimacion ${est} supera el tope ${args.max_cost}.")
        return 1

    from dotenv import load_dotenv

    from agent.cli import require_api_key
    from agent.paths import ENV_FILE

    load_dotenv(ENV_FILE)  # siempre el .env de la raiz del proyecto
    require_api_key()
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d")
    suffix = f"_{args.backend}" if args.backend != "table" else ""
    out_path = args.output or f"evals/results_{stamp}_{MODEL}{suffix}_n{args.n}.json"
    results = run_matrix(cells, args.n, out_path, backend=args.backend)
    print_table(results["table"])
    print(f"\nResultados congelados en: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
